import os
import sqlite3
import hashlib
import hmac
import json
import random
import ssl
import socket
import datetime
import ast
import operator
import math
import re
import secrets
import time
from contextlib import contextmanager
from collections import defaultdict, deque
from typing import Optional, Tuple
import urllib.request
import urllib.error
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, abort
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
configured_secret = os.environ.get("FLASK_SECRET_KEY")
if not configured_secret:
    secret_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".flask_secret_key")
    try:
        with open(secret_path, "r", encoding="utf-8") as secret_file:
            configured_secret = secret_file.read().strip()
    except FileNotFoundError:
        configured_secret = secrets.token_hex(32)
        try:
            with open(secret_path, "x", encoding="utf-8") as secret_file:
                secret_file.write(configured_secret)
        except FileExistsError:
            with open(secret_path, "r", encoding="utf-8") as secret_file:
                configured_secret = secret_file.read().strip()
app.secret_key = configured_secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
    MAX_CONTENT_LENGTH=32 * 1024,
)
DB_NAME = os.environ.get(
    "CAMPUS_ESCAPE_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "campus_escape.db"),
)
AUTH_ATTEMPTS = defaultdict(deque)
CODE_ATTEMPTS = defaultdict(deque)
VAULT_ATTEMPTS = defaultdict(deque)
FEEDBACK_ATTEMPTS = defaultdict(deque)

def rate_limited(bucket, key, limit, window_seconds):
    now = time.time()
    attempts = bucket[key]
    while attempts and attempts[0] <= now - window_seconds:
        attempts.popleft()
    if len(attempts) >= limit:
        return True
    attempts.append(now)
    return False

def json_data():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}

def load_active_quiz():
    quiz_id = session.get("quiz_id")
    username = session.get("username")
    if not quiz_id or not username:
        return None
    with get_db() as conn:
        row = conn.execute("SELECT payload FROM active_quizzes WHERE quiz_id = ? AND username = ?", (quiz_id, username)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload"])
    except (TypeError, json.JSONDecodeError):
        clear_active_quiz()
        return None

def save_active_quiz(quiz):
    quiz_id = session.get("quiz_id") or secrets.token_urlsafe(24)
    username = session.get("username")
    if not username:
        return
    session["quiz_id"] = quiz_id
    with get_db() as conn:
        conn.execute(
            "INSERT INTO active_quizzes (quiz_id, username, payload, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(quiz_id) DO UPDATE SET payload = excluded.payload, updated_at = CURRENT_TIMESTAMP",
            (quiz_id, username, json.dumps(quiz)),
        )
        conn.commit()

def clear_active_quiz():
    quiz_id = session.pop("quiz_id", None)
    if quiz_id:
        with get_db() as conn:
            conn.execute("DELETE FROM active_quizzes WHERE quiz_id = ?", (quiz_id,))
            conn.commit()

# ==========================================
# MAIN QUIZ QUESTION BANK (10 questions per run, sampled from 12)
# ==========================================

QUESTION_BANK = {
    "python": [
        {"id": 1, "topic": "Arithmetic", "difficulty": "Starter", "text": "What does this expression print?", "code": "n = 6\nprint(n // 4)", "opts": ["1", "2", "1.5", "0"], "correct": 0, "hint": "The // operator performs floor division.", "exp": "6 divided by 4 is 1.5; floor division rounds down to 1."},
        {"id": 2, "topic": "Strings", "difficulty": "Core", "text": "Which text is printed by this slice?", "code": "print('code'[1:3])", "opts": ["od", "co", "de", "code"], "correct": 0, "hint": "A slice includes its start index and stops before its end index.", "exp": "Indexes 1 and 2 contain 'o' and 'd', so the result is 'od'."},
        {"id": 3, "topic": "Lists", "difficulty": "Starter", "text": "What is the list length after this code runs?", "code": "items = ['map', 'key']\nitems.append('door')\nprint(len(items))", "opts": ["2", "3", "4", "Error"], "correct": 1, "hint": "append() adds one item to the end of the list.", "exp": "The list starts with two items and append() adds one, so its length is 3."},
        {"id": 4, "topic": "Loops", "difficulty": "Core", "text": "How many values does this loop visit?", "code": "for n in range(2, 8, 2):\n    print(n)", "opts": ["2", "3", "4", "6"], "correct": 1, "hint": "Write out the values generated before the stop value 8.", "exp": "The loop visits 2, 4, and 6: three values."},
        {"id": 5, "topic": "Booleans", "difficulty": "Core", "text": "What is printed by this Boolean expression?", "code": "print(not (4 < 2))", "opts": ["True", "False", "0", "Error"], "correct": 0, "hint": "First evaluate the comparison inside the parentheses.", "exp": "4 < 2 is False, and not False is True."},
        {"id": 6, "topic": "Dictionaries", "difficulty": "Challenge", "text": "What value is printed for the 'name' key?", "code": "agent = {'name': 'Mina'}\nprint(agent['name'])", "opts": ["agent", "name", "Mina", "Error"], "correct": 2, "hint": "A dictionary lookup uses the key to retrieve its value.", "exp": "The value stored under the key 'name' is 'Mina'."},
        {"id": 7, "topic": "Operators", "difficulty": "Starter", "text": "What is the result of this expression?", "code": "print(2 ** 3 + 1)", "opts": ["9", "16", "17", "7"], "correct": 0, "hint": "Exponentiation happens before addition.", "exp": "2 ** 3 is 8; adding 1 gives 9."},
        {"id": 8, "topic": "Types", "difficulty": "Core", "text": "What number is printed after converting the text to an integer?", "code": "print(int('8') + 2)", "opts": ["82", "10", "8", "Error"], "correct": 1, "hint": "int('8') converts the digit text into the integer 8.", "exp": "The calculation is 8 + 2, which equals 10."},
        {"id": 9, "topic": "Functions", "difficulty": "Challenge", "text": "What does double(6) return?", "code": "def double(value):\n    return value * 2\n\nprint(double(6))", "opts": ["8", "12", "36", "6"], "correct": 1, "hint": "Substitute 6 for value, then multiply by 2.", "exp": "double(6) returns 6 * 2, which is 12."},
        {"id": 10, "topic": "Indexing", "difficulty": "Core", "text": "Which value does index -2 select?", "code": "points = [4, 9, 12]\nprint(points[-2])", "opts": ["4", "9", "12", "-2"], "correct": 1, "hint": "Negative indexes count backward from the end; -1 is last.", "exp": "-2 selects the second-to-last element, 9."},
        {"id": 11, "topic": "Conditions", "difficulty": "Starter", "text": "Which word is printed when score is 70?", "code": "score = 70\nif score >= 60:\n    print('Pass')\nelse:\n    print('Retry')", "opts": ["Pass", "Retry", "70", "Nothing"], "correct": 0, "hint": "Check whether 70 is greater than or equal to 60.", "exp": "70 >= 60 is True, so the program prints Pass."},
        {"id": 12, "topic": "Remainders", "difficulty": "Core", "text": "What remainder does this expression produce?", "code": "print(17 % 5)", "opts": ["2", "3", "5", "0"], "correct": 0, "hint": "% returns what remains after whole-number division.", "exp": "5 fits into 17 three times (15), leaving a remainder of 2."}
    ],
    "c": [
        {"id": 1, "topic": "Precedence", "difficulty": "Starter", "text": "What value is printed?", "code": "printf(\"%d\", 7 + 2 * 3);", "opts": ["27", "13", "21", "15"], "correct": 1, "hint": "Multiplication is evaluated before addition.", "exp": "2 * 3 is 6, then 7 + 6 gives 13."},
        {"id": 2, "topic": "Integer Division", "difficulty": "Core", "text": "What does integer division print?", "code": "int count = 17;\nprintf(\"%d\", count / 5);", "opts": ["3", "3.4", "4", "2"], "correct": 0, "hint": "Both operands are integers, so the fractional part is discarded.", "exp": "17 / 5 is 3 with a remainder; integer division prints 3."},
        {"id": 3, "topic": "Arrays", "difficulty": "Core", "text": "Which value is stored at index 2?", "code": "int values[] = {2, 4, 6, 8};\nprintf(\"%d\", values[2]);", "opts": ["4", "6", "8", "2"], "correct": 1, "hint": "Array indexes start at 0.", "exp": "Index 0 is 2, index 1 is 4, and index 2 is 6."},
        {"id": 4, "topic": "Remainders", "difficulty": "Starter", "text": "What does the remainder operator return here?", "code": "printf(\"%d\", 19 % 6);", "opts": ["1", "2", "3", "6"], "correct": 0, "hint": "Find the amount left after subtracting the largest multiple of 6 below 19.", "exp": "6 * 3 is 18; 19 - 18 leaves 1."},
        {"id": 5, "topic": "Loops", "difficulty": "Starter", "text": "How many times does this loop run?", "code": "for (int i = 1; i <= 3; i++) {\n    printf(\"%d \", i);\n}", "opts": ["2", "3", "4", "1"], "correct": 1, "hint": "The values are 1, 2, and 3; the condition includes 3.", "exp": "The loop runs once for each value 1 through 3: three times."},
        {"id": 6, "topic": "Assignment", "difficulty": "Core", "text": "What value is printed after the compound assignment?", "code": "int score = 5;\nscore += 3;\nprintf(\"%d\", score);", "opts": ["5", "8", "3", "15"], "correct": 1, "hint": "+= adds the right-hand value to the current variable.", "exp": "score becomes 5 + 3, which is 8."},
        {"id": 7, "topic": "Conditions", "difficulty": "Core", "text": "Which word is printed?", "code": "int temperature = 31;\nif (temperature > 30) printf(\"Warm\");\nelse printf(\"Cool\");", "opts": ["Warm", "Cool", "31", "Nothing"], "correct": 0, "hint": "Compare 31 with 30 using the greater-than operator.", "exp": "31 > 30 is true, so Warm is printed."},
        {"id": 8, "topic": "Types and Output", "difficulty": "Challenge", "text": "What appears with the one-decimal format?", "code": "float price = 2.5f;\nprintf(\"%.1f\", price);", "opts": ["2", "2.5", "2.50", "Error"], "correct": 1, "hint": "%.1f displays a floating-point value with one digit after the decimal point.", "exp": "The value is 2.5, which already has one decimal place."},
        {"id": 9, "topic": "Characters", "difficulty": "Challenge", "text": "Which character is printed?", "code": "char letter = 'A';\nprintf(\"%c\", letter + 2);", "opts": ["A", "B", "C", "2"], "correct": 2, "hint": "In the basic character set, adding 1 moves to the next letter.", "exp": "Adding 2 to 'A' advances two character codes to 'C'."},
        {"id": 10, "topic": "Functions", "difficulty": "Challenge", "text": "What value does add(4, 5) return?", "code": "int add(int a, int b) { return a + b; }\nprintf(\"%d\", add(4, 5));", "opts": ["9", "20", "1", "45"], "correct": 0, "hint": "The function returns the sum of its two arguments.", "exp": "4 + 5 equals 9."},
        {"id": 11, "topic": "Array Length", "difficulty": "Core", "text": "How many elements are in this array?", "code": "int codes[] = {7, 8, 9};\nint count = sizeof(codes) / sizeof(codes[0]);", "opts": ["2", "3", "4", "The byte size"], "correct": 1, "hint": "The total array size divided by one element's size gives the element count.", "exp": "The array contains three integers, so the expression evaluates to 3."},
        {"id": 12, "topic": "Increment", "difficulty": "Starter", "text": "What is printed after the increment?", "code": "int lives = 2;\nlives++;\nprintf(\"%d\", lives);", "opts": ["1", "2", "3", "0"], "correct": 2, "hint": "The ++ operator increases the variable by one.", "exp": "lives starts at 2 and becomes 3."}
    ],
    "logic": [
        {"id": 1, "topic": "Sequences", "difficulty": "Starter", "text": "Choose the next number in the sequence.", "code": "4, 8, 16, 32, ?", "opts": ["48", "56", "64", "72"], "correct": 2, "hint": "Each term is multiplied by the same number.", "exp": "Each term doubles, so 32 * 2 = 64."},
        {"id": 2, "topic": "Proportions", "difficulty": "Core", "text": "Three notebooks cost ₹45. At the same price per notebook, what do four cost?", "code": "", "opts": ["₹50", "₹55", "₹60", "₹75"], "correct": 2, "hint": "Find the cost of one notebook first.", "exp": "Each notebook costs ₹15, so four cost 4 * ₹15 = ₹60."},
        {"id": 3, "topic": "Deduction", "difficulty": "Challenge", "text": "All programmers in a club solve puzzles. Mira solves puzzles. What can you conclude?", "code": "", "opts": ["Mira is definitely a programmer", "Mira is not a programmer", "There is not enough information", "Everyone in the club is Mira"], "correct": 2, "hint": "The statement gives one direction: programmers solve puzzles. Does it say only programmers solve puzzles?", "exp": "Puzzle solving does not prove someone is a programmer; other people may solve puzzles too."},
        {"id": 4, "topic": "Averages", "difficulty": "Core", "text": "What is the average of 8, 12, and 16?", "code": "", "opts": ["10", "12", "14", "36"], "correct": 1, "hint": "Add the values, then divide by how many values there are.", "exp": "(8 + 12 + 16) / 3 = 36 / 3 = 12."},
        {"id": 5, "topic": "Percentages", "difficulty": "Starter", "text": "What is 25% of 240?", "code": "", "opts": ["40", "50", "60", "80"], "correct": 2, "hint": "25% is one quarter.", "exp": "240 / 4 = 60."},
        {"id": 6, "topic": "Sequences", "difficulty": "Challenge", "text": "Choose the next number: 2, 5, 11, 23, ?", "code": "", "opts": ["35", "47", "46", "48"], "correct": 1, "hint": "Look for a repeated operation using the previous term.", "exp": "Each term is doubled and then 1 is added: 23 * 2 + 1 = 47."},
        {"id": 7, "topic": "Counting", "difficulty": "Core", "text": "Five people each shake hands with every other person once. How many handshakes occur?", "code": "", "opts": ["5", "10", "15", "20"], "correct": 1, "hint": "Count unique pairs; a handshake between A and B is the same as B and A.", "exp": "There are 5 * 4 / 2 = 10 unique pairs."},
        {"id": 8, "topic": "Calendar", "difficulty": "Starter", "text": "If today is Friday, what day will it be three days later?", "code": "", "opts": ["Sunday", "Monday", "Tuesday", "Wednesday"], "correct": 1, "hint": "Count the days after Friday: Saturday is day 1.", "exp": "Saturday, Sunday, then Monday: three days later is Monday."},
        {"id": 9, "topic": "Ordering", "difficulty": "Core", "text": "Asha is older than Ben. Ben is older than Chao. Who is youngest?", "code": "", "opts": ["Asha", "Ben", "Chao", "Cannot tell"], "correct": 2, "hint": "Put the three people in order from oldest to youngest.", "exp": "Asha > Ben > Chao, so Chao is youngest."},
        {"id": 10, "topic": "Probability", "difficulty": "Challenge", "text": "A fair spinner has four equal sections, and one is blue. What is the chance of landing on blue?", "code": "", "opts": ["1 in 2", "1 in 3", "1 in 4", "4 in 1"], "correct": 2, "hint": "There is one favorable section out of four equally likely sections.", "exp": "The probability is 1/4, or one chance in four."},
        {"id": 11, "topic": "Rates", "difficulty": "Challenge", "text": "Two workers finish a task in six hours. At the same rate, how long would four workers take?", "code": "", "opts": ["2 hours", "3 hours", "6 hours", "12 hours"], "correct": 1, "hint": "Twice as many workers need half the time for the same work.", "exp": "Doubling workers from 2 to 4 halves six hours to three hours."},
        {"id": 12, "topic": "Time", "difficulty": "Starter", "text": "What time is 45 minutes after 2:35?", "code": "", "opts": ["3:10", "3:15", "3:20", "3:25"], "correct": 2, "hint": "Add 25 minutes to reach 3:00, then add the remaining 20 minutes.", "exp": "2:35 + 45 minutes = 3:20."}
    ]
}

# ==========================================
# DEBUG LAB CHALLENGES DATA STRUCTURE
# ==========================================

DEBUG_CHALLENGES = {
    "python": [
        {
            "id": "py001",
            "title": "BUG #01 — Fix the Sum",
            "difficulty": 1,
            "description": "Fix the code so that it prints the sum of a and b.",
            "starterCode": "a = 10\nb = 20\n\nprint(a - b)",
            "expectedOutput": "30",
            "hints": [
                "Look closely at the arithmetic operator used on line 4.",
                "Compare the minus sign (-) with what the task asks you to calculate.",
                "Replace the subtraction operator (-) with the addition operator (+)."
            ],
            "xp": 100
        },
        {
            "id": "py002",
            "title": "BUG #02 — Missing Indentation",
            "difficulty": 1,
            "description": "Fix the code so that the print statement executes inside the loop block.",
            "starterCode": "for i in range(3):\nprint('Iteration:', i)",
            "expectedOutput": "Iteration: 0\nIteration: 1\nIteration: 2",
            "hints": [
                "Python relies on spaces or tabs to define code blocks.",
                "Look at line 2. It is aligned flush with the left margin.",
                "Indent line 2 by adding 4 spaces before the print function."
            ],
            "xp": 100
        },
        {
            "id": "py003",
            "title": "BUG #03 — Fix the Type Error",
            "difficulty": 2,
            "description": "Fix the program so that it prints the string 'Age: 20' without raising a TypeError.",
            "starterCode": "age = 20\nprint('Age: ' + age)",
            "expectedOutput": "Age: 20",
            "hints": [
                "Python cannot concatenate a string and an integer directly using +.",
                "Convert the integer variable 'age' into a string before adding it.",
                "Wrap age with the str() function: str(age), or use a comma in print()."
            ],
            "xp": 150
        },
        {
            "id": "py004",
            "title": "BUG #04 — List Range Bounds",
            "difficulty": 2,
            "description": "Fix the loop range so that it prints all 4 elements in the list.",
            "starterCode": "items = ['Python', 'C', 'Logic', 'Security']\nfor i in range(3):\n    print(items[i])",
            "expectedOutput": "Python\nC\nLogic\nSecurity",
            "hints": [
                "Count how many elements exist inside the 'items' list.",
                "range(3) generates indices 0, 1, and 2 (only 3 total iterations).",
                "Change range(3) to range(4) or range(len(items))."
            ],
            "xp": 150
        },
        {
            "id": "py005",
            "title": "BUG #05 — Wrong Comparison Operator",
            "difficulty": 3,
            "description": "Fix the conditional check so that it prints 'Pass' when score is 50 or above.",
            "starterCode": "score = 50\nif score < 50:\n    print('Pass')\nelse:\n    print('Fail')",
            "expectedOutput": "Pass",
            "hints": [
                "Read the conditional statement: if score < 50.",
                "The task asks for a passing grade when score is 50 or above.",
                "Change score < 50 to score >= 50."
            ],
            "xp": 200
        },
        {
            "id": "py006",
            "title": "BUG #06 — Running Total",
            "difficulty": 2,
            "description": "Fix the accumulator so it adds every score and prints 20.",
            "starterCode": "scores = [5, 7, 8]\ntotal = 0\nfor score in scores:\n    total =+ score\nprint(total)",
            "expectedOutput": "20",
            "hints": [
                "The loop visits all three scores, but the total does not keep its previous value.",
                "Look closely at the two-character operator on the total assignment.",
                "Use total += score to add each score to the running total."
            ],
            "xp": 200
        },
        {
            "id": "py007",
            "title": "BUG #07 — Power Up",
            "difficulty": 2,
            "description": "Fix the arithmetic so the program prints 49, the square of 7.",
            "starterCode": "number = 7\nprint(number + 2)",
            "expectedOutput": "49",
            "hints": [
                "The current line adds a value instead of squaring the number.",
                "Python uses a special operator for exponentiation.",
                "Replace the addition with number ** 2."
            ],
            "xp": 200
        },
        {
            "id": "py008",
            "title": "BUG #08 — Find the Final Item",
            "difficulty": 3,
            "description": "Fix the list index so the program prints blue.",
            "starterCode": "colors = ['red', 'green', 'blue']\nprint(colors[1])",
            "expectedOutput": "blue",
            "hints": [
                "List positions start at zero, not one.",
                "The final item in a list of three is at index 2.",
                "Change colors[1] to colors[2]."
            ],
            "xp": 250
        }
    ],
    "c": [
        {
            "id": "c001",
            "title": "BUG #01 — Wrong Format Specifier",
            "difficulty": 1,
            "description": "Fix the format specifier in printf so that it correctly prints the integer x.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    int x = 25;\n    printf(\"%f\\n\", x);\n    return 0;\n}",
            "expectedOutput": "25",
            "hints": [
                "Look at the format specifier %f inside printf.",
                "%f is used for floating-point numbers, but x is declared as int.",
                "Replace %f with %d."
            ],
            "xp": 100
        },
        {
            "id": "c002",
            "title": "BUG #02 — Missing Semicolon",
            "difficulty": 1,
            "description": "Fix the syntax error so that the program compiles cleanly.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    printf(\"Campus Escape\\n\")\n    return 0;\n}",
            "expectedOutput": "Campus Escape",
            "hints": [
                "C requires statement terminators at the end of every line.",
                "Look at line 4.",
                "Add a semicolon ';' to the end of the printf line."
            ],
            "xp": 100
        },
        {
            "id": "c003",
            "title": "BUG #03 — Assignment vs Equality",
            "difficulty": 2,
            "description": "Fix the if statement so it performs a comparison rather than assignment.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    int key = 4;\n    if (key = 5) {\n        printf(\"UNLOCKED\\n\");\n    } else {\n        printf(\"LOCKED\\n\");\n    }\n    return 0;\n}",
            "expectedOutput": "LOCKED",
            "hints": [
                "A single '=' assigns a value; it does not test equality.",
                "To test if key equals 5 in C, you must use the relational equality operator.",
                "Change if (key = 5) to if (key == 5)."
            ],
            "xp": 150
        },
        {
            "id": "c004",
            "title": "BUG #04 — Wrong Variable in Printf",
            "difficulty": 3,
            "description": "Fix the program so it prints the value stored in num.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    int num = 42;\n    printf(\"Value: %d\\n\", number);\n    return 0;\n}",
            "expectedOutput": "Value: 42",
            "hints": [
                "Check the variable name inside printf.",
                "Ensure the format specifier matches the variable.",
                "Replace the misspelled variable 'number' with the declared variable 'num'."
            ],
            "xp": 200
        },
        {
            "id": "c005",
            "title": "BUG #05 — Array Index Boundary",
            "difficulty": 3,
            "description": "Fix the loop start so it prints all 3 values, beginning with 10.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    int arr[3] = {10, 20, 30};\n    for(int i = 1; i < 3; i++) {\n        printf(\"%d\\n\", arr[i]);\n    }\n    return 0;\n}",
            "expectedOutput": "10\n20\n30",
            "hints": [
                "An array starts at index 0, so its first value is arr[0].",
                "The loop currently begins at index 1 and skips the first value.",
                "Change the loop start from i = 1 to i = 0."
            ],
            "xp": 200
        },
        {
            "id": "c006",
            "title": "BUG #06 — Complete the Loop",
            "difficulty": 2,
            "description": "Fix the loop so it prints all four values, including 40.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    int values[4] = {10, 20, 30, 40};\n    for (int i = 0; i < 3; i++) {\n        printf(\"%d\\n\", values[i]);\n    }\n    return 0;\n}",
            "expectedOutput": "10\n20\n30\n40",
            "hints": [
                "The array has four elements and valid indexes 0 through 3.",
                "The loop currently stops when i reaches 3, before printing that element.",
                "Change i < 3 to i < 4."
            ],
            "xp": 200
        },
        {
            "id": "c007",
            "title": "BUG #07 — Unit Conversion",
            "difficulty": 2,
            "description": "Convert 3 kilometres to metres and print 3000.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    int distanceKm = 3;\n    int distanceMeters = distanceKm + 1000;\n    printf(\"%d\\n\", distanceMeters);\n    return 0;\n}",
            "expectedOutput": "3000",
            "hints": [
                "One kilometre contains 1000 metres.",
                "Adding 1000 only increases the value by one kilometre.",
                "Multiply distanceKm by 1000."
            ],
            "xp": 200
        },
        {
            "id": "c008",
            "title": "BUG #08 — Correct the Calculation",
            "difficulty": 3,
            "description": "Fix the function so multiply(6, 7) prints 42.",
            "starterCode": "#include <stdio.h>\n\nint multiply(int a, int b) {\n    return a + b;\n}\n\nint main() {\n    printf(\"%d\\n\", multiply(6, 7));\n    return 0;\n}",
            "expectedOutput": "42",
            "hints": [
                "The function is named multiply, but its return expression adds.",
                "Check which arithmetic operation combines a and b.",
                "Return a * b."
            ],
            "xp": 250
        }
    ]
}

# Explicitly requested solutions are returned only by the answer endpoint.
DEBUG_SOLUTIONS = {
    "py001": "a = 10\nb = 20\nprint(a + b)",
    "py002": "for i in range(3):\n    print('Iteration:', i)",
    "py003": "age = 20\nprint(f'Age: {age}')",
    "py004": "items = ['Python', 'C', 'Logic', 'Security']\nfor i in range(len(items)):\n    print(items[i])",
    "py005": "score = 50\nif score >= 50:\n    print('Pass')\nelse:\n    print('Fail')",
    "py006": "scores = [5, 7, 8]\ntotal = 0\nfor score in scores:\n    total += score\nprint(total)",
    "py007": "number = 7\nprint(number ** 2)",
    "py008": "colors = ['red', 'green', 'blue']\nprint(colors[2])",
    "c001": "#include <stdio.h>\n\nint main() {\n    int x = 25;\n    printf(\"%d\\n\", x);\n    return 0;\n}",
    "c002": "#include <stdio.h>\n\nint main() {\n    printf(\"Campus Escape\\n\");\n    return 0;\n}",
    "c003": "#include <stdio.h>\n\nint main() {\n    int key = 4;\n    if (key == 5) {\n        printf(\"UNLOCKED\\n\");\n    } else {\n        printf(\"LOCKED\\n\");\n    }\n    return 0;\n}",
    "c004": "#include <stdio.h>\n\nint main() {\n    int num = 42;\n    printf(\"Value: %d\\n\", num);\n    return 0;\n}",
    "c005": "#include <stdio.h>\n\nint main() {\n    int arr[3] = {10, 20, 30};\n    for (int i = 0; i < 3; i++) {\n        printf(\"%d\\n\", arr[i]);\n    }\n    return 0;\n}",
    "c006": "#include <stdio.h>\n\nint main() {\n    int values[4] = {10, 20, 30, 40};\n    for (int i = 0; i < 4; i++) {\n        printf(\"%d\\n\", values[i]);\n    }\n    return 0;\n}",
    "c007": "#include <stdio.h>\n\nint main() {\n    int distanceKm = 3;\n    int distanceMeters = distanceKm * 1000;\n    printf(\"%d\\n\", distanceMeters);\n    return 0;\n}",
    "c008": "#include <stdio.h>\n\nint multiply(int a, int b) {\n    return a * b;\n}\n\nint main() {\n    printf(\"%d\\n\", multiply(6, 7));\n    return 0;\n}"
}

# Database Helpers
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def generate_player_id(cursor) -> str:
    """Return a stable, human-readable unique public account reference."""
    while True:
        player_id = f"CE-{secrets.token_hex(5).upper()}"
        if cursor.execute("SELECT 1 FROM users WHERE player_id = ?", (player_id,)).fetchone() is None:
            return player_id

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                player_id TEXT,
                pin_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'player',
                best_score INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                python_score INTEGER DEFAULT 0,
                c_score INTEGER DEFAULT 0,
                logic_score INTEGER DEFAULT 0,
                total_score INTEGER DEFAULT 0,
                accuracy REAL DEFAULT 0,
                played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_progress (
                username TEXT PRIMARY KEY,
                vault_cleared INTEGER NOT NULL DEFAULT 0,
                vault_cleared_at TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sector_completions (
                username TEXT NOT NULL,
                level TEXT NOT NULL,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (username, level)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS debug_progress (
                username TEXT NOT NULL,
                challenge_id TEXT NOT NULL,
                xp INTEGER NOT NULL,
                solved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (username, challenge_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_quizzes (
                quiz_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                player_id TEXT NOT NULL,
                channel TEXT NOT NULL CHECK(channel IN ('admin', 'community')),
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                hidden INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                player_id TEXT NOT NULL,
                message TEXT NOT NULL,
                hidden INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        feedback_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(feedback)").fetchall()}
        if "player_id" not in feedback_columns:
            cursor.execute("ALTER TABLE feedback ADD COLUMN player_id TEXT")
        reply_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(feedback_replies)").fetchall()}
        if "player_id" not in reply_columns:
            cursor.execute("ALTER TABLE feedback_replies ADD COLUMN player_id TEXT")
        cursor.execute("DELETE FROM active_quizzes WHERE updated_at < datetime('now', '-1 day')")
        user_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(users)").fetchall()}
        if "player_id" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN player_id TEXT")
        if "role" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'player'")
        for user_row in cursor.execute("SELECT id FROM users WHERE player_id IS NULL OR player_id = ''").fetchall():
            cursor.execute(
                "UPDATE users SET player_id = ? WHERE id = ?",
                (generate_player_id(cursor), user_row["id"]),
            )
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_player_id ON users(player_id)")
        cursor.execute("""
            UPDATE feedback SET player_id = COALESCE(
                (SELECT player_id FROM users WHERE users.username = feedback.username), 'FORMER'
            ) WHERE player_id IS NULL OR player_id = ''
        """)
        cursor.execute("""
            UPDATE feedback_replies SET player_id = COALESCE(
                (SELECT player_id FROM users WHERE users.username = feedback_replies.username), 'FORMER'
            ) WHERE player_id IS NULL OR player_id = ''
        """)
        admin = cursor.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", ("Ranjith123",)).fetchone()
        if admin:
            cursor.execute("UPDATE users SET role = 'admin' WHERE id = ?", (admin["id"],))
        else:
            cursor.execute(
                "INSERT INTO users (username, player_id, pin_hash, role) VALUES (?, ?, ?, 'admin')",
                ("Ranjith123", generate_player_id(cursor), generate_password_hash("1234", method="pbkdf2:sha256", salt_length=16)),
            )
        result_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(results)").fetchall()}
        if "level" not in result_columns:
            cursor.execute("ALTER TABLE results ADD COLUMN level TEXT")
        if "run_score" not in result_columns:
            cursor.execute("ALTER TABLE results ADD COLUMN run_score INTEGER DEFAULT 0")
        for level, score_column in (("python", "python_score"), ("c", "c_score"), ("logic", "logic_score")):
            cursor.execute(
                f"INSERT OR IGNORE INTO sector_completions (username, level) "
                f"SELECT DISTINCT username, ? FROM results WHERE {score_column} > 0",
                (level,),
            )
        conn.commit()

init_db()

def hash_pin(pin: str) -> str:
    return generate_password_hash(pin, method="pbkdf2:sha256", salt_length=16)

def verify_pin(stored_hash: str, pin: str) -> Tuple[bool, Optional[str]]:
    # Upgrade existing SHA-256 records to a salted, deliberately slow hash at login.
    if re.fullmatch(r"[0-9a-f]{64}", stored_hash or ""):
        valid = secrets.compare_digest(stored_hash, hashlib.sha256(pin.encode()).hexdigest())
        return valid, hash_pin(pin) if valid else None
    try:
        return check_password_hash(stored_hash, pin), None
    except (ValueError, TypeError):
        return False, None

# ==========================================
# CODE EXECUTION RUNNERS
# ==========================================

class SafePythonRunner:
    """Small AST interpreter for beginner exercises; never executes Python bytecode."""
    binary_ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
                  ast.Mod: operator.mod, ast.Pow: operator.pow}
    compare_ops = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                   ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}

    def __init__(self):
        self.env = {}
        self.output = []
        self.steps = 0

    def step(self):
        self.steps += 1
        if self.steps > 20000:
            raise ValueError("Execution limit reached (20,000 operations).")

    def binary(self, op, left, right):
        if isinstance(op, ast.Pow) and (not isinstance(right, (int, float)) or abs(right) > 100):
            raise ValueError("Exponent limit is 100.")
        if isinstance(op, ast.Mult) and isinstance(left, (str, list, tuple)) and isinstance(right, int):
            if len(left) * max(0, right) > 10000:
                raise ValueError("Value is too large (10,000 items/characters maximum).")
        if isinstance(op, ast.Mult) and isinstance(right, (str, list, tuple)) and isinstance(left, int):
            if len(right) * max(0, left) > 10000:
                raise ValueError("Value is too large (10,000 items/characters maximum).")
        value = self.binary_ops[type(op)](left, right)
        if isinstance(value, (str, list, tuple)) and len(value) > 10000:
            raise ValueError("Value is too large (10,000 items/characters maximum).")
        if isinstance(value, (int, float)) and (abs(value) > 10**100 or (isinstance(value, float) and not math.isfinite(value))):
            raise ValueError("Numeric value is too large.")
        return value

    def expression(self, node):
        self.step()
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool, type(None))):
            if isinstance(node.value, int) and abs(node.value) > 10**100:
                raise ValueError("Numeric value is too large.")
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                elif isinstance(value, ast.FormattedValue) and value.conversion == -1 and value.format_spec is None:
                    parts.append(str(self.expression(value.value)))
                else:
                    raise ValueError("Only simple f-string values such as f'Age: {age}' are supported.")
            return "".join(parts)
        if isinstance(node, ast.Name):
            if node.id not in self.env:
                raise NameError(f"name '{node.id}' is not defined")
            return self.env[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [self.expression(item) for item in node.elts]
            return values if isinstance(node, ast.List) else tuple(values)
        if isinstance(node, ast.BinOp) and type(node.op) in self.binary_ops:
            left, right = self.expression(node.left), self.expression(node.right)
            return self.binary(node.op, left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Not)):
            value = self.expression(node.operand)
            return +value if isinstance(node.op, ast.UAdd) else (-value if isinstance(node.op, ast.USub) else not value)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [self.expression(item) for item in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = self.expression(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = self.expression(comparator)
                if type(op) not in self.compare_ops or not self.compare_ops[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Subscript):
            return self.expression(node.value)[self.expression(node.slice)]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            args = [self.expression(arg) for arg in node.args]
            name = node.func.id
            if name == "print":
                self.output.append(" ".join(map(str, args)))
                if sum(map(len, self.output)) > 16000:
                    raise ValueError("Output limit reached (16,000 characters).")
                return None
            if name == "range" and 1 <= len(args) <= 3:
                result = range(*args)
                if len(result) > 10000:
                    raise ValueError("Loop limit reached (10,000 iterations).")
                return result
            safe_calls = {"len": len, "str": str, "int": int, "float": float, "abs": abs}
            if name in safe_calls and len(args) == 1:
                return safe_calls[name](*args)
        raise ValueError(f"Unsupported Python feature: {type(node).__name__}.")

    def statements(self, nodes):
        for node in nodes:
            self.step()
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                self.env[node.targets[0].id] = self.expression(node.value)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and type(node.op) in self.binary_ops:
                self.env[node.target.id] = self.binary(node.op, self.env[node.target.id], self.expression(node.value))
            elif isinstance(node, ast.Expr):
                self.expression(node.value)
            elif isinstance(node, ast.If):
                self.statements(node.body if self.expression(node.test) else node.orelse)
            elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                values = self.expression(node.iter)
                if not isinstance(values, (range, list, tuple, str)) or len(values) > 10000:
                    raise ValueError("Only lists, tuples, strings, and range() with up to 10,000 items can be looped over.")
                for value in values:
                    self.env[node.target.id] = value
                    self.statements(node.body)
                self.statements(node.orelse)
            else:
                raise ValueError(f"Unsupported Python statement: {type(node).__name__}.")

def run_python_code(source_code: str, timeout_sec: int = 4):
    if not isinstance(source_code, str) or len(source_code) > 12000:
        return {"success": False, "stdout": "", "stderr": "Code must be 12,000 characters or fewer.", "exit_code": 1}
    try:
        tree = ast.parse(source_code, mode="exec")
        runner = SafePythonRunner()
        runner.statements(tree.body)
        return {"success": True, "stdout": "\n".join(runner.output), "stderr": "", "exit_code": 0}
    except (SyntaxError, NameError, TypeError, ValueError, ZeroDivisionError, IndexError, KeyError, OverflowError, ArithmeticError, RecursionError) as exc:
        return {"success": False, "stdout": "", "stderr": str(exc), "exit_code": 1}

def run_c_code(source_code: str, timeout_sec: int = 5):
    if not isinstance(source_code, str) or len(source_code) > 12000:
        return {"success": False, "stdout": "", "stderr": "Code must be 12,000 characters or fewer.", "exit_code": 1}

    # Use Wandbox's public sandboxed compiler. Piston's public hosted API
    # started requiring authorization in 2026, so unauthenticated C runs fail.
    url = "https://wandbox.org/api/compile.json"
    payload = json.dumps({
        "compiler": os.environ.get("WANDBOX_C_COMPILER", "gcc-head-c"),
        "code": source_code,
        "stdin": "",
        "options": "warning",
        "compiler-option-raw": "-std=c11",
        "runtime-option-raw": "",
        "save": False,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "CampusEscape/2.1 (educational C debugging lab)",
    }

    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=max(timeout_sec, 12), context=ssl.create_default_context()) as response:
            result = json.loads(response.read().decode("utf-8"))

        compiler_exit = result.get("compiler_exit_code", 0)
        if compiler_exit not in (0, None, "0"):
            diagnostics = result.get("compiler_error") or result.get("compiler_output") or "Compilation failed."
            return {"success": False, "stdout": "", "stderr": f"COMPILATION ERROR:\n{diagnostics.strip()}", "exit_code": 1}

        stdout = result.get("program_output") or ""
        stderr = result.get("program_error") or ""
        exit_code = result.get("program_exit_code", 0)
        if exit_code is None:
            exit_code = 0
        try:
            exit_code = int(exit_code)
        except (TypeError, ValueError):
            exit_code = 1

        # Some compiler backends report a timeout/signal separately from an exit code.
        status = str(result.get("status") or "").upper()
        signal = result.get("signal")
        if signal or status not in ("", "0", "OK", "SUCCESS"):
            details = stderr.strip() or result.get("program_error") or f"Program stopped ({status or signal})."
            return {"success": False, "stdout": stdout.strip(), "stderr": details, "exit_code": exit_code or 1}

        return {"success": exit_code == 0, "stdout": stdout.strip(), "stderr": stderr.strip(), "exit_code": exit_code}
    except urllib.error.HTTPError as exc:
        body = exc.read(2048).decode("utf-8", errors="replace").strip()
        if exc.code == 429:
            message = "The C compiler is busy. Wait a moment and run your code again."
        elif exc.code in (401, 403):
            message = "The online C compiler rejected the request. Check the compiler service configuration."
        else:
            message = f"The C compiler service returned HTTP {exc.code}."
        if body:
            message += f"\n{body}"
        return {"success": False, "stdout": "", "stderr": message, "exit_code": -1}
    except (TimeoutError, socket.timeout):
        return {"success": False, "stdout": "", "stderr": "The C compiler took too long to respond. Try again shortly.", "exit_code": -1}
    except (urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        reason = getattr(exc, "reason", None)
        detail = str(reason or exc)
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Could not reach the online C compiler ({detail}). Check your internet connection and try again.",
            "exit_code": -1
        }

# ==========================================
# PAGE ROUTING
# ==========================================

@app.route("/")
def index():
    return render_template("index.html")
@app.route("/map")
def map_page():
    if "username" not in session:
        return redirect(url_for("index"))

    with get_db() as conn:
        cursor = conn.cursor()
        account = cursor.execute("SELECT player_id, role FROM users WHERE username = ?", (session["username"],)).fetchone()
        cursor.execute("""
            SELECT MAX(python_score) as py_s, MAX(c_score) as c_s, MAX(logic_score) as log_s
            FROM results WHERE username = ?
        """, (session["username"],))
        res = cursor.fetchone()

    py_s = res["py_s"] if res and res["py_s"] is not None else 0
    c_s = res["c_s"] if res and res["c_s"] is not None else 0
    log_s = res["log_s"] if res and res["log_s"] is not None else 0

    tot_score = py_s + c_s + log_s
    accuracy = round((tot_score / 3000.0) * 100, 2) if tot_score > 0 else 0.0

    is_admin = bool(account and account["role"] == "admin")
    python_completed = sector_is_completed(session["username"], "python")
    c_completed = sector_is_completed(session["username"], "c")
    unlock_c = is_admin or python_completed
    unlock_logic = is_admin or (python_completed and c_completed)
    logic_completed = sector_is_completed(session["username"], "logic")
    all_completed = is_admin or all_sectors_completed(session["username"])
    vault_cleared = vault_is_cleared(session["username"]) or (is_admin and session.get("admin_vault_preview", False))
    progress_percent = round(sum(sector_is_completed(session["username"], level) for level in QUESTION_BANK) / 3 * 100)

    return render_template(
        "map.html",
        username=session["username"],
        player_id=account["player_id"] if account else "—",
        is_admin=is_admin,
        python_completed=python_completed,
        py_s=py_s, c_s=c_s, log_s=log_s,
        unlock_c=unlock_c,
        unlock_logic=unlock_logic,
        c_completed=c_completed,
        logic_completed=logic_completed,
        all_completed=all_completed,
        vault_cleared=vault_cleared,
        progress_percent=progress_percent,
        accuracy=accuracy
    )

def get_best_scores(username):
    with get_db() as conn:
        row = conn.execute(
            "SELECT MAX(python_score) AS py, MAX(c_score) AS c, MAX(logic_score) AS logic FROM results WHERE username = ?",
            (username,),
        ).fetchone()
    return tuple((row[key] or 0) if row else 0 for key in ("py", "c", "logic"))

def level_is_unlocked(username, level):
    if username == session.get("username") and is_current_admin():
        return True
    return level == "python" or (level == "c" and sector_is_completed(username, "python")) or (
        level == "logic" and sector_is_completed(username, "python") and sector_is_completed(username, "c")
    )

def sector_is_completed(username, level):
    with get_db() as conn:
        row = conn.execute("SELECT 1 FROM sector_completions WHERE username = ? AND level = ?", (username, level)).fetchone()
    return bool(row)

def all_sectors_completed(username):
    return all(sector_is_completed(username, level) for level in QUESTION_BANK)

def vault_is_cleared(username):
    with get_db() as conn:
        row = conn.execute("SELECT vault_cleared FROM user_progress WHERE username = ?", (username,)).fetchone()
    return bool(row and row["vault_cleared"])

@app.route("/certificate")
def certificate():
    if "username" not in session:
        return redirect(url_for("index"))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT MAX(python_score) as py_s, MAX(c_score) as c_s, MAX(logic_score) as log_s
            FROM results WHERE username = ?
        """, (session["username"],))
        res = cursor.fetchone()

    py_s = res["py_s"] if res and res["py_s"] is not None else 0
    c_s = res["c_s"] if res and res["c_s"] is not None else 0
    log_s = res["log_s"] if res and res["log_s"] is not None else 0
    accuracy = round((py_s + c_s + log_s) / 3000 * 100, 2)

    admin_preview = is_current_admin()
    requirements_met = all_sectors_completed(session["username"]) and vault_is_cleared(session["username"])
    if not requirements_met and not admin_preview:
        return redirect(url_for("map_page"))

    with get_db() as conn:
        progress = conn.execute(
            "SELECT vault_cleared_at FROM user_progress WHERE username = ?",
            (session["username"],),
        ).fetchone()
        debug_xp_row = conn.execute(
            "SELECT COALESCE(SUM(xp), 0) AS xp FROM debug_progress WHERE username = ?",
            (session["username"],),
        ).fetchone()

    cleared_at = progress["vault_cleared_at"] if progress else None
    try:
        issued = datetime.datetime.strptime(cleared_at, "%Y-%m-%d %H:%M:%S") if cleared_at else datetime.datetime.now()
    except (TypeError, ValueError):
        issued = datetime.datetime.now()
    today_date = issued.strftime("%B %d, %Y")
    certificate_id = ("PREVIEW-" if admin_preview else "CE-") + hmac.new(
        app.secret_key.encode("utf-8"),
        f"{session['username']}:{cleared_at or today_date}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:12].upper()

    return render_template(
        "certificate.html",
        username=session["username"],
        accuracy=accuracy,
        date=today_date,
        certificate_id=certificate_id,
        python_score=py_s,
        c_score=c_s,
        logic_score=log_s,
        total_score=py_s + c_s + log_s,
        debug_xp=debug_xp_row["xp"] if debug_xp_row else 0,
        admin_preview=admin_preview,
    )

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("index"))

    with get_db() as conn:
        user_info = conn.execute(
            "SELECT player_id, best_score, attempts, role FROM users WHERE username = ?",
            (session["username"],),
        ).fetchone()
        history = conn.execute("""
            SELECT python_score, c_score, logic_score, total_score, accuracy, played_at, level, run_score
            FROM results WHERE username = ? ORDER BY id DESC LIMIT 5
        """, (session["username"],)).fetchall()

    if not user_info:
        session.clear()
        return redirect(url_for("index"))
    latest = history[0] if history else None
    best_score = user_info["best_score"] or 0
    attempts = user_info["attempts"] or 0
    py_best, c_best, logic_best = get_best_scores(session["username"])
    with get_db() as conn:
        debug_xp_row = conn.execute("SELECT COALESCE(SUM(xp), 0) AS xp FROM debug_progress WHERE username = ?", (session["username"],)).fetchone()
    debug_xp = debug_xp_row["xp"] if debug_xp_row else 0
    achievements = [
        {"icon": "🐍", "name": "Python Pioneer", "description": "Complete your first Python mission.", "unlocked": sector_is_completed(session["username"], "python")},
        {"icon": "🧩", "name": "Polyglot", "description": "Clear both Python and C sectors.", "unlocked": sector_is_completed(session["username"], "python") and sector_is_completed(session["username"], "c")},
        {"icon": "🚀", "name": "Escape Artist", "description": "Clear all three learning sectors.", "unlocked": all_sectors_completed(session["username"])},
        {"icon": "💯", "name": "Perfect Run", "description": "Earn 1,000 points in any sector.", "unlocked": max(py_best, c_best, logic_best) >= 1000},
        {"icon": "🔁", "name": "Persistent", "description": "Complete five quiz attempts.", "unlocked": attempts >= 5},
        {"icon": "🛠️", "name": "Bug Hunter", "description": "Earn 700 XP in the debugging lab.", "unlocked": debug_xp >= 700},
    ]

    sectors = [
        {"name": "Python", "best": py_best, "cleared": sector_is_completed(session["username"], "python")},
        {"name": "C Programming", "best": c_best, "cleared": sector_is_completed(session["username"], "c")},
        {"name": "Logic", "best": logic_best, "cleared": sector_is_completed(session["username"], "logic")},
    ]

    return render_template(
        "dashboard.html",
        player_id=user_info["player_id"],
        is_admin=user_info["role"] == "admin",
        best_score=best_score,
        attempts=attempts,
        latest=latest,
        history=history,
        achievements=achievements,
        debug_xp=debug_xp,
        sectors=sectors,
        accuracy=latest["accuracy"] if latest and latest["accuracy"] is not None else 0,
    )

def is_current_admin():
    username = session.get("username")
    if not username:
        return False
    with get_db() as conn:
        row = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
    return bool(row and row["role"] == "admin")

def require_admin_api():
    if "username" not in session:
        return jsonify({"success": False, "message": "Sign in required."}), 401
    if not is_current_admin():
        return jsonify({"success": False, "message": "Administrator access required."}), 403
    supplied = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(supplied, expected):
        return jsonify({"success": False, "message": "Session security check failed. Reload the admin page."}), 400
    return None

@app.route("/admin")
def admin_dashboard():
    if "username" not in session:
        return redirect(url_for("index"))
    if not is_current_admin():
        abort(403)
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    with get_db() as conn:
        stats = conn.execute("""
            SELECT COUNT(*) AS accounts, COALESCE(SUM(attempts), 0) AS attempts,
                   COALESCE(SUM(best_score), 0) AS combined_best
            FROM users
        """).fetchone()
        feedback_stats = conn.execute("""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN status = 'open' AND hidden = 0 THEN 1 ELSE 0 END), 0) AS open
            FROM feedback
        """).fetchone()
        accounts = conn.execute("""
            SELECT u.username, u.player_id, u.role, u.best_score, u.attempts, u.created_at,
                   (SELECT COUNT(*) FROM sector_completions s WHERE s.username = u.username) AS sectors,
                   (SELECT COALESCE(SUM(d.xp), 0) FROM debug_progress d WHERE d.username = u.username) AS debug_xp,
                   (SELECT COALESCE(v.vault_cleared, 0) FROM user_progress v WHERE v.username = u.username) AS vault_cleared
            FROM users u ORDER BY u.created_at DESC, u.id DESC
        """).fetchall()
    return render_template("admin.html", accounts=accounts, stats=stats, feedback_stats=feedback_stats, admin_username=session["username"], csrf_token=csrf_token)

@app.route("/api/admin/accounts/<player_id>/reset", methods=["POST"])
def admin_reset_account(player_id):
    auth_error = require_admin_api()
    if auth_error:
        return auth_error
    with get_db() as conn:
        target = conn.execute("SELECT username, role FROM users WHERE player_id = ?", (player_id,)).fetchone()
        if not target:
            return jsonify({"success": False, "message": "Account not found."}), 404
        if target["role"] == "admin":
            return jsonify({"success": False, "message": "Administrator accounts are protected."}), 403
        username = target["username"]
        for table in ("results", "user_progress", "sector_completions", "debug_progress", "active_quizzes"):
            conn.execute(f'DELETE FROM "{table}" WHERE username = ?', (username,))
        conn.execute("UPDATE users SET best_score = 0, attempts = 0 WHERE username = ?", (username,))
        conn.commit()
    return jsonify({"success": True, "message": f"Progress reset for {player_id}."})

@app.route("/api/admin/accounts/<player_id>/delete", methods=["POST"])
def admin_delete_account(player_id):
    auth_error = require_admin_api()
    if auth_error:
        return auth_error
    with get_db() as conn:
        target = conn.execute("SELECT username, role FROM users WHERE player_id = ?", (player_id,)).fetchone()
        if not target:
            return jsonify({"success": False, "message": "Account not found."}), 404
        if target["role"] == "admin":
            return jsonify({"success": False, "message": "Administrator accounts are protected."}), 403
        username = target["username"]
        for table in ("results", "user_progress", "sector_completions", "debug_progress", "active_quizzes"):
            conn.execute(f'DELETE FROM "{table}" WHERE username = ?', (username,))
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
    return jsonify({"success": True, "message": f"Account {player_id} deleted."})

@app.route("/api/admin/qa/complete", methods=["POST"])
def admin_qa_complete():
    auth_error = require_admin_api()
    if auth_error:
        return auth_error
    data = json_data()
    sector = data.get("sector")
    prerequisites = {
        "python": ["python"],
        "c": ["python", "c"],
        "logic": ["python", "c", "logic"],
        "all": ["python", "c", "logic"],
    }
    if sector not in prerequisites:
        return jsonify({"success": False, "message": "Select a valid lab."}), 400
    username = session["username"]
    with get_db() as conn:
        for level in prerequisites[sector]:
            conn.execute("INSERT OR IGNORE INTO sector_completions (username, level) VALUES (?, ?)", (username, level))
        # QA completion is a navigation shortcut, not a vault or certificate award.
        conn.execute("DELETE FROM user_progress WHERE username = ?", (username,))
        conn.commit()
    session.pop("admin_vault_preview", None)
    label = "all labs" if sector == "all" else f"{sector.title()} Lab and its prerequisites"
    return jsonify({"success": True, "message": f"QA access enabled for {label}. No score or certificate was awarded."})

@app.route("/api/admin/qa/reset", methods=["POST"])
def admin_qa_reset():
    auth_error = require_admin_api()
    if auth_error:
        return auth_error
    username = session["username"]
    with get_db() as conn:
        for table in ("results", "user_progress", "sector_completions", "debug_progress", "active_quizzes"):
            conn.execute(f'DELETE FROM "{table}" WHERE username = ?', (username,))
        conn.execute("UPDATE users SET best_score = 0, attempts = 0 WHERE username = ?", (username,))
        conn.commit()
    session.pop("admin_vault_preview", None)
    return jsonify({"success": True, "message": "Your admin QA progress has been reset."})

@app.route("/api/admin/qa/debug-complete", methods=["POST"])
def admin_qa_debug_complete():
    auth_error = require_admin_api()
    if auth_error:
        return auth_error
    data = json_data()
    language = data.get("language")
    if language not in ("python", "c", "all"):
        return jsonify({"success": False, "message": "Select Python, C, or all debug challenges."}), 400
    languages = ("python", "c") if language == "all" else (language,)
    username = session["username"]
    with get_db() as conn:
        for lang in languages:
            for challenge in DEBUG_CHALLENGES[lang]:
                conn.execute(
                    "INSERT OR IGNORE INTO debug_progress (username, challenge_id, xp) VALUES (?, ?, ?)",
                    (username, challenge["id"], challenge["xp"]),
                )
        conn.commit()
    return jsonify({"success": True, "message": "Selected debugging challenges marked mastered on your admin account."})

def attach_feedback_replies(conn, threads, include_hidden=False, viewer_is_admin=False):
    result = []
    for row in threads:
        thread = dict(row)
        sql = """
            SELECT r.id, r.username, r.message, r.hidden, r.created_at,
                   COALESCE(u.player_id, 'Former member') AS author_id
            FROM feedback_replies r LEFT JOIN users u ON u.player_id = r.player_id
            WHERE r.feedback_id = ?
        """
        if not include_hidden:
            sql += " AND r.hidden = 0"
        sql += " ORDER BY r.created_at, r.id"
        thread["replies"] = [dict(reply) for reply in conn.execute(sql, (thread["id"],)).fetchall()]
        thread["author_id"] = thread.get("author_id") or "Former member"
        thread["can_reply"] = thread["channel"] == "community" or viewer_is_admin or thread["username"] == session.get("username")
        result.append(thread)
    return result

@app.route("/feedback")
def feedback_page():
    if "username" not in session:
        return redirect(url_for("index"))
    admin = is_current_admin()
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    with get_db() as conn:
        query = """
            SELECT f.*, COALESCE(u.player_id, f.player_id, 'Former member') AS author_id
            FROM feedback f LEFT JOIN users u ON u.player_id = f.player_id
        """
        params = ()
        if not admin:
            query += " WHERE f.hidden = 0 AND (f.channel = 'community' OR f.player_id = (SELECT player_id FROM users WHERE username = ?))"
            params = (session["username"],)
        query += " ORDER BY f.created_at DESC, f.id DESC LIMIT 150"
        rows = conn.execute(query, params).fetchall()
        threads = attach_feedback_replies(conn, rows, viewer_is_admin=admin)
        profile = conn.execute("SELECT player_id FROM users WHERE username = ?", (session["username"],)).fetchone()
    return render_template("feedback.html", threads=threads, is_admin=admin, csrf_token=csrf_token,
                           account_username=session["username"], account_player_id=profile["player_id"] if profile else "—")

@app.route("/api/feedback", methods=["POST"])
def submit_feedback():
    if "username" not in session:
        return jsonify({"success": False, "message": "Sign in to send feedback."}), 401
    expected = session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), expected):
        return jsonify({"success": False, "message": "Reload the feedback page and try again."}), 400
    if rate_limited(FEEDBACK_ATTEMPTS, session["username"], 5, 300):
        return jsonify({"success": False, "message": "Please wait a few minutes before sending more feedback."}), 429
    data = json_data()
    channel = data.get("channel")
    subject = data.get("subject", "").strip() if isinstance(data.get("subject", ""), str) else ""
    message = data.get("message", "").strip() if isinstance(data.get("message", ""), str) else ""
    if channel not in ("admin", "community"):
        return jsonify({"success": False, "message": "Choose Admin inbox or Community."}), 400
    if len(subject) > 100 or not message or len(message) > 2000:
        return jsonify({"success": False, "message": "The title can be up to 100 characters and the message must contain 1–2,000 characters."}), 400
    if not subject:
        subject = "Private feedback" if channel == "admin" else "Community post"
    with get_db() as conn:
        player = conn.execute("SELECT player_id FROM users WHERE username = ?", (session["username"],)).fetchone()
        if not player:
            return jsonify({"success": False, "message": "Account not found. Please sign in again."}), 401
        conn.execute(
            "INSERT INTO feedback (username, player_id, channel, subject, message) VALUES (?, ?, ?, ?, ?)",
            (session["username"], player["player_id"], channel, subject, message),
        )
        conn.commit()
    return jsonify({"success": True, "message": "Sent to the admin inbox." if channel == "admin" else "Posted to the community."})

@app.route("/api/feedback/<int:feedback_id>/reply", methods=["POST"])
def reply_to_feedback(feedback_id):
    if "username" not in session:
        return jsonify({"success": False, "message": "Sign in to reply."}), 401
    expected = session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), expected):
        return jsonify({"success": False, "message": "Reload the feedback page and try again."}), 400
    if rate_limited(FEEDBACK_ATTEMPTS, f"reply:{session['username']}", 12, 300):
        return jsonify({"success": False, "message": "Please wait before sending more replies."}), 429
    data = json_data()
    message = data.get("message", "")
    if not isinstance(message, str) or not message.strip() or len(message.strip()) > 1200:
        return jsonify({"success": False, "message": "Reply must contain 1–1,200 characters."}), 400
    admin = is_current_admin()
    with get_db() as conn:
        thread = conn.execute("SELECT username, player_id, channel, hidden FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
        if not thread or (thread["hidden"] and not admin):
            return jsonify({"success": False, "message": "Feedback thread not found."}), 404
        player = conn.execute("SELECT player_id FROM users WHERE username = ?", (session["username"],)).fetchone()
        if not player:
            return jsonify({"success": False, "message": "Account not found. Please sign in again."}), 401
        if thread["channel"] == "admin" and not admin and thread["player_id"] != player["player_id"]:
            return jsonify({"success": False, "message": "This message is private to its sender and the admin."}), 403
        conn.execute(
            "INSERT INTO feedback_replies (feedback_id, username, player_id, message) VALUES (?, ?, ?, ?)",
            (feedback_id, session["username"], player["player_id"], message.strip()),
        )
        if not admin:
            conn.execute("UPDATE feedback SET status = 'open' WHERE id = ?", (feedback_id,))
        conn.commit()
    return jsonify({"success": True, "message": "Reply posted."})

@app.route("/admin/feedback")
def admin_feedback_page():
    if "username" not in session:
        return redirect(url_for("index"))
    if not is_current_admin():
        abort(403)
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    with get_db() as conn:
        rows = conn.execute("""
            SELECT f.*, COALESCE(u.player_id, f.player_id, 'Former member') AS author_id
            FROM feedback f LEFT JOIN users u ON u.player_id = f.player_id
            ORDER BY f.created_at DESC, f.id DESC LIMIT 300
        """).fetchall()
        threads = attach_feedback_replies(conn, rows, include_hidden=True, viewer_is_admin=True)
        profile = conn.execute("SELECT player_id FROM users WHERE username = ?", (session["username"],)).fetchone()
    return render_template("admin_feedback.html", threads=threads, csrf_token=csrf_token,
                           account_username=session["username"], account_player_id=profile["player_id"] if profile else "—")

@app.route("/api/admin/feedback/<int:feedback_id>/moderate", methods=["POST"])
def moderate_feedback(feedback_id):
    auth_error = require_admin_api()
    if auth_error:
        return auth_error
    action = json_data().get("action")
    updates = {"resolve": ("status", "resolved"), "reopen": ("status", "open"), "hide": ("hidden", 1), "restore": ("hidden", 0)}
    if action not in updates:
        return jsonify({"success": False, "message": "Unsupported moderation action."}), 400
    column, value = updates[action]
    with get_db() as conn:
        cursor = conn.execute(f"UPDATE feedback SET {column} = ? WHERE id = ?", (value, feedback_id))
        if not cursor.rowcount:
            return jsonify({"success": False, "message": "Feedback thread not found."}), 404
        conn.commit()
    return jsonify({"success": True, "message": "Feedback updated."})

@app.route("/api/admin/feedback/replies/<int:reply_id>/moderate", methods=["POST"])
def moderate_feedback_reply(reply_id):
    auth_error = require_admin_api()
    if auth_error:
        return auth_error
    action = json_data().get("action")
    if action not in ("hide", "restore"):
        return jsonify({"success": False, "message": "Unsupported moderation action."}), 400
    with get_db() as conn:
        cursor = conn.execute("UPDATE feedback_replies SET hidden = ? WHERE id = ?", (1 if action == "hide" else 0, reply_id))
        if not cursor.rowcount:
            return jsonify({"success": False, "message": "Reply not found."}), 404
        conn.commit()
    return jsonify({"success": True, "message": "Reply updated."})

@app.route("/api/admin/feedback/<int:feedback_id>/delete", methods=["POST"])
def delete_feedback(feedback_id):
    auth_error = require_admin_api()
    if auth_error:
        return auth_error
    with get_db() as conn:
        exists = conn.execute("SELECT 1 FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
        if not exists:
            return jsonify({"success": False, "message": "Feedback post not found."}), 404
        conn.execute("DELETE FROM feedback_replies WHERE feedback_id = ?", (feedback_id,))
        conn.execute("DELETE FROM feedback WHERE id = ?", (feedback_id,))
        conn.commit()
    return jsonify({"success": True, "message": "Feedback post and its replies were deleted."})

@app.route("/api/admin/feedback/replies/<int:reply_id>/delete", methods=["POST"])
def delete_feedback_reply(reply_id):
    auth_error = require_admin_api()
    if auth_error:
        return auth_error
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM feedback_replies WHERE id = ?", (reply_id,))
        if not cursor.rowcount:
            return jsonify({"success": False, "message": "Reply not found."}), 404
        conn.commit()
    return jsonify({"success": True, "message": "Reply deleted."})

@app.route("/game")
def python_game():
    if "username" not in session:
        return redirect(url_for("index"))
    return render_template("game.html", level_id="python", level_name="Python Lab", level_num="1")

@app.route("/c")
def c_game():
    if "username" not in session:
        return redirect(url_for("index"))
    if not level_is_unlocked(session["username"], "c"):
        return redirect(url_for("map_page"))
    return render_template("game.html", level_id="c", level_name="C Programming Lab", level_num="2")

@app.route("/logic")
def logic_game():
    if "username" not in session:
        return redirect(url_for("index"))
    if not level_is_unlocked(session["username"], "logic"):
        return redirect(url_for("map_page"))
    return render_template("game.html", level_id="logic", level_name="Logic Lab", level_num="3")

@app.route("/lock")
def password_room():
    if "username" not in session:
        return redirect(url_for("index"))
    py_score, c_score, logic_score = get_best_scores(session["username"])
    if not all_sectors_completed(session["username"]) and not is_current_admin():
        return redirect(url_for("map_page"))
    return render_template("lock.html")

@app.route("/debug")
def debug_select():
    if "username" not in session:
        return redirect(url_for("index"))
    return render_template(
        "debug_select.html",
        username=session["username"],
        python_challenge_count=len(DEBUG_CHALLENGES["python"]),
        c_challenge_count=len(DEBUG_CHALLENGES["c"]),
    )

@app.route("/debug/<lang>")
def debug_lab(lang):
    if "username" not in session:
        return redirect(url_for("index"))
    if lang not in ["python", "c"]:
        return redirect(url_for("debug_select"))
    return render_template("debug_lab.html", lang=lang, username=session["username"])

# ==========================================
# API ENDPOINTS
# ==========================================

@app.route("/api/auth", methods=["POST"])
def auth():
    data = json_data()
    identifier = data.get("identifier", data.get("username", ""))
    pin = data.get("pin", "")
    client_key = request.remote_addr or "unknown"
    if rate_limited(AUTH_ATTEMPTS, client_key, 8, 600):
        return jsonify({"success": False, "message": "Too many sign-in attempts. Wait 10 minutes and try again."}), 429
    if not isinstance(identifier, str) or not isinstance(pin, str):
        return jsonify({"success": False, "message": "Enter your username or account ID, and your 4-digit PIN."}), 400
    identifier, pin = identifier.strip(), pin.strip()
    is_player_id = bool(re.fullmatch(r"CE-[A-Fa-f0-9]{10}", identifier))
    is_id_like = identifier.upper().startswith("CE-")
    if not is_player_id and not re.fullmatch(r"[A-Za-z0-9_.-]{3,24}", identifier):
        return jsonify({"success": False, "message": "Enter a valid username or ID (CE- followed by 10 letters or numbers)."}), 400
    if not re.fullmatch(r"\d{4}", pin):
        return jsonify({"success": False, "message": "PIN must contain exactly 4 digits."}), 400

    with get_db() as conn:
        cursor = conn.cursor()
        if is_player_id:
            cursor.execute("SELECT * FROM users WHERE player_id = ? COLLATE NOCASE", (identifier.upper(),))
        elif is_id_like:
            return jsonify({"success": False, "message": "That account ID is not recognized. Check the ID and try again."}), 401
        else:
            cursor.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (identifier,))
        user = cursor.fetchone()

        if user:
            valid, upgraded_hash = verify_pin(user["pin_hash"], pin)
            if not valid:
                return jsonify({"success": False, "message": "Invalid PIN for user."}), 401
            if upgraded_hash:
                cursor.execute("UPDATE users SET pin_hash = ? WHERE id = ?", (upgraded_hash, user["id"]))
                conn.commit()
            session.clear()
            session["username"] = user["username"]
            AUTH_ATTEMPTS.pop(client_key, None)
            return jsonify({"success": True, "message": "Login successful!"})
        else:
            if is_player_id:
                return jsonify({"success": False, "message": "That account ID is not recognized. Check the ID and try again."}), 401
            cursor.execute(
                "INSERT INTO users (username, player_id, pin_hash) VALUES (?, ?, ?)",
                (identifier, generate_player_id(cursor), hash_pin(pin)),
            )
            conn.commit()
            session.clear()
            session["username"] = identifier
            return jsonify({"success": True, "message": "Account created!"})

@app.route("/api/get-questions/<level>", methods=["GET"])
def get_questions(level):
    if "username" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    if level not in QUESTION_BANK:
        return jsonify({"success": False, "message": "Invalid level."}), 400
    if not level_is_unlocked(session["username"], level):
        return jsonify({"success": False, "message": "Complete the previous sector to unlock this mission."}), 403

    sample_size = min(10, len(QUESTION_BANK[level]))
    questions = random.sample(QUESTION_BANK[level], sample_size)
    # Keep answer keys and hints on the server. The browser receives only display data.
    clear_active_quiz()
    quiz = {
        "level": level,
        "questions": [{"id": q["id"], "correct": q["correct"], "hint": q["hint"], "exp": q["exp"]} for q in questions],
        "index": 0,
        "score": 0,
        "lives": 3,
        "answered": False,
        "deadline": time.time() + 45,
        "hint_used": False,
    }
    save_active_quiz(quiz)
    public_questions = [{k: v for k, v in q.items() if k not in ("correct", "hint", "exp")} for q in questions]
    return jsonify({"success": True, "questions": public_questions})

@app.route("/api/quiz/hint", methods=["POST"])
def quiz_hint():
    quiz = load_active_quiz()
    if "username" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    if not quiz or quiz["answered"] or quiz["index"] >= len(quiz["questions"]):
        return jsonify({"success": False, "message": "No active question."}), 400
    if not quiz["hint_used"]:
        quiz["hint_used"] = True
        save_active_quiz(quiz)
    return jsonify({"success": True, "hint": quiz["questions"][quiz["index"]]["hint"]})

@app.route("/api/quiz/answer", methods=["POST"])
def quiz_answer():
    quiz = load_active_quiz()
    if "username" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    if not quiz or quiz["answered"] or quiz["index"] >= len(quiz["questions"]):
        return jsonify({"success": False, "message": "No answer is expected right now."}), 400
    data = json_data()
    selected = data.get("answer")
    current = quiz["questions"][quiz["index"]]
    timed_out = time.time() > quiz["deadline"]
    if timed_out:
        selected = None
    if selected is not None and (type(selected) is not int or not 0 <= selected < 4):
        return jsonify({"success": False, "message": "Choose one of the listed answers."}), 400
    correct = isinstance(selected, int) and selected == current["correct"]
    quiz["answered"] = True
    if correct:
        quiz["score"] += 100
    else:
        quiz["lives"] -= 1
    save_active_quiz(quiz)
    return jsonify({
        "success": True, "correct": correct, "correct_index": current["correct"],
        "score": quiz["score"], "lives": quiz["lives"], "explanation": current["exp"],
        "timed_out": timed_out or selected is None,
    })

@app.route("/api/quiz/next", methods=["POST"])
def quiz_next():
    quiz = load_active_quiz()
    if "username" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    if not quiz or not quiz["answered"] or quiz["index"] >= len(quiz["questions"]):
        return jsonify({"success": False, "message": "Answer the current question first."}), 400
    quiz["index"] += 1
    quiz["answered"] = False
    quiz["deadline"] = time.time() + 45
    quiz["hint_used"] = False
    save_active_quiz(quiz)
    return jsonify({"success": True, "finished": quiz["index"] >= len(quiz["questions"]) or quiz["lives"] <= 0})

@app.route("/api/save-score", methods=["POST"])
def save_score():
    if "username" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    data = json_data()
    quiz = load_active_quiz()
    level = data.get("level")
    if not quiz or level != quiz.get("level") or (quiz["index"] < len(quiz["questions"]) and quiz["lives"] > 0):
        return jsonify({"success": False, "message": "No completed quiz score to save."}), 400
    sector_cleared = quiz["index"] >= len(quiz["questions"]) and quiz["lives"] > 0
    py_score = quiz["score"] if level == "python" else 0
    c_score = quiz["score"] if level == "c" else 0
    logic_score = quiz["score"] if level == "logic" else 0

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT python_score, c_score, logic_score FROM results
            WHERE username = ? ORDER BY id DESC LIMIT 1
        """, (session["username"],))
        existing = cursor.fetchone()

        new_py = max(py_score, existing["python_score"] if existing else 0)
        new_c = max(c_score, existing["c_score"] if existing else 0)
        new_log = max(logic_score, existing["logic_score"] if existing else 0)

        tot = new_py + new_c + new_log
        accuracy = round((tot / 3000.0) * 100, 2)

        cursor.execute("""
            INSERT INTO results (username, python_score, c_score, logic_score, total_score, accuracy, level, run_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (session["username"], new_py, new_c, new_log, tot, accuracy, level, quiz["score"]))

        cursor.execute("""
            UPDATE users SET best_score = MAX(best_score, ?), attempts = attempts + 1 WHERE username = ?
        """, (tot, session["username"]))
        conn.commit()

    if sector_cleared:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sector_completions (username, level) VALUES (?, ?)",
                (session["username"], level),
            )
            conn.commit()

    clear_active_quiz()
    return jsonify({"success": True, "total_score": tot, "accuracy": accuracy, "sector_cleared": sector_cleared})

@app.route("/api/debug/challenges/<lang>", methods=["GET"])
def get_debug_challenges(lang):
    if "username" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    if lang not in DEBUG_CHALLENGES:
        return jsonify({"success": False, "message": "Language not supported."}), 400
    safe_challenges = [
        {key: value for key, value in challenge.items() if key != "expectedOutput"}
        for challenge in DEBUG_CHALLENGES[lang]
    ]
    return jsonify({"success": True, "challenges": safe_challenges})

@app.route("/api/debug/progress", methods=["GET"])
def get_debug_progress():
    if "username" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    with get_db() as conn:
        rows = conn.execute("SELECT challenge_id, xp FROM debug_progress WHERE username = ?", (session["username"],)).fetchall()
    return jsonify({"success": True, "xp": sum(row["xp"] for row in rows), "solved": [row["challenge_id"] for row in rows]})

@app.route("/api/debug/solution", methods=["POST"])
def get_debug_solution():
    if "username" not in session:
        return jsonify({"success": False, "message": "Sign in to view challenge answers."}), 401
    if rate_limited(CODE_ATTEMPTS, f"solution:{request.remote_addr or 'unknown'}", 20, 60):
        return jsonify({"success": False, "message": "Too many requests. Wait a minute and try again."}), 429
    data = json_data()
    challenge_id = data.get("challenge_id", "")
    solution = DEBUG_SOLUTIONS.get(challenge_id) if isinstance(challenge_id, str) else None
    if solution is None:
        return jsonify({"success": False, "message": "Challenge answer not found."}), 404
    return jsonify({"success": True, "solution": solution})

@app.route("/api/debug/run", methods=["POST"])
def run_code():
    if "username" not in session:
        return jsonify({"success": False, "message": "Sign in to use the code runner."}), 401
    if rate_limited(CODE_ATTEMPTS, request.remote_addr or "unknown", 20, 60):
        return jsonify({"success": False, "message": "Runner limit reached. Wait a minute and try again."}), 429
    data = json_data()
    lang_value = data.get("language", "python")
    lang = lang_value.lower() if isinstance(lang_value, str) else ""
    code = data.get("code", "")

    if lang == "python":
        res = run_python_code(code)
    elif lang == "c":
        res = run_c_code(code)
    else:
        return jsonify({"success": False, "message": "Unsupported language."}), 400

    return jsonify(res)

@app.route("/api/debug/submit", methods=["POST"])
def submit_code():
    if "username" not in session:
        return jsonify({"success": False, "message": "Sign in to submit code."}), 401
    if rate_limited(CODE_ATTEMPTS, request.remote_addr or "unknown", 20, 60):
        return jsonify({"success": False, "message": "Runner limit reached. Wait a minute and try again."}), 429
    data = json_data()
    lang_value = data.get("language", "python")
    lang = lang_value.lower() if isinstance(lang_value, str) else ""
    challenge_id = data.get("challenge_id", "")
    code = data.get("code", "")

    challenges = DEBUG_CHALLENGES.get(lang, [])
    target = next((c for c in challenges if c["id"] == challenge_id), None)

    if not target:
        return jsonify({"success": False, "message": "Challenge ID not found."}), 404

    if lang not in ("python", "c"):
        return jsonify({"success": False, "message": "Unsupported language."}), 400
    res = run_python_code(code) if lang == "python" else run_c_code(code)

    if not res["success"]:
        return jsonify({
            "passed": False,
            "stdout": res["stdout"],
            "stderr": res["stderr"],
            "message": "Execution failed or raised an error."
        })

    actual_output = res["stdout"].strip()
    expected_output = target["expectedOutput"].strip()

    passed = (actual_output == expected_output)

    awarded_xp = 0
    total_xp = 0
    if passed:
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO debug_progress (username, challenge_id, xp) VALUES (?, ?, ?)",
                (session["username"], challenge_id, target["xp"]),
            )
            awarded_xp = target["xp"] if cursor.rowcount else 0
            xp_row = conn.execute("SELECT COALESCE(SUM(xp), 0) AS total FROM debug_progress WHERE username = ?", (session["username"],)).fetchone()
            total_xp = xp_row["total"] if xp_row else 0
            conn.commit()

    return jsonify({
        "passed": passed,
        "stdout": actual_output,
        "stderr": res["stderr"],
        "expected": expected_output,
        "xp": awarded_xp,
        "total_xp": total_xp,
        "message": "✓ ALL TEST CASES PASSED!" if passed else "✗ Output does not match expected output."
    })

@app.route("/api/explain-error", methods=["POST"])
def explain_error():
    if "username" not in session:
        return jsonify({"success": False, "message": "Sign in to use the code coach."}), 401
    data = json_data()
    code = data.get("code", "")
    error_msg = data.get("stderr", "")
    error_msg = error_msg if isinstance(error_msg, str) else ""
    lang_value = data.get("language", "python")
    lang = lang_value.lower() if isinstance(lang_value, str) else "python"

    if not error_msg:
        return jsonify({"success": True, "explanation": "Code compiled cleanly without stderr errors."})

    err_lower = error_msg.lower()

    if "expected ';'" in err_lower:
        explanation = "C requires a semicolon (;) at the end of statements. Check your statement endings."
    elif "format '%d' expects argument" in err_lower or "format '%f'" in err_lower:
        explanation = "Format specifier mismatch: %d is for integers (int), %f is for floating points (float), and %s is for strings."
    elif "indentationerror" in err_lower:
        explanation = "Python relies on indentation for block structure. Ensure statements inside loops/conditionals are indented by 4 spaces."
    elif "typeerror" in err_lower:
        explanation = "Type error detected. Check if you are concatenating numbers and strings directly without str() conversion."
    elif "syntaxerror" in err_lower:
        explanation = "Syntax error detected. Check your parenthesis, brackets, or colon usage."
    else:
        explanation = f"Compiler flagged an issue: {error_msg.splitlines()[0] if error_msg else 'Review line syntax.'}"

    return jsonify({"success": True, "explanation": explanation})

@app.route("/api/vault/verify", methods=["POST"])
def verify_vault_key():
    if "username" not in session:
        return jsonify({"success": False, "message": "Sign in to access the vault."}), 401
    client_key = request.remote_addr or "unknown"
    if rate_limited(VAULT_ATTEMPTS, client_key, 10, 300):
        return jsonify({"success": False, "message": "Too many attempts. Wait a few minutes and try again."}), 429
    if not all_sectors_completed(session["username"]) and not is_current_admin():
        return jsonify({"success": False, "message": "Clear all three sectors before opening the vault."}), 403
    data = json_data()
    entered_key = data.get("key", "")
    if not isinstance(entered_key, str) or len(entered_key) > 32 or not secrets.compare_digest(entered_key.strip(), "556"):
        return jsonify({"success": False, "message": "The cipher is incorrect. Check each sector result and try again."}), 400
    admin_preview = is_current_admin()
    if admin_preview:
        session["admin_vault_preview"] = True
    else:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO user_progress (username, vault_cleared, vault_cleared_at) VALUES (?, 1, CURRENT_TIMESTAMP) "
                "ON CONFLICT(username) DO UPDATE SET vault_cleared = 1, "
                "vault_cleared_at = COALESCE(user_progress.vault_cleared_at, CURRENT_TIMESTAMP)",
                (session["username"],),
            )
            conn.commit()
    VAULT_ATTEMPTS.pop(client_key, None)
    message = "Vault preview cleared. The certificate remains marked as an admin preview." if admin_preview else "Vault cleared. Your certificate is ready!"
    return jsonify({"success": True, "message": message})

@app.route("/api/admin/delete-user/<target_user>", methods=["DELETE"])
def delete_user(target_user):
    if "username" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    current_user = session["username"]
    if current_user != target_user:
        return jsonify({"success": False, "message": "You can only delete your own account."}), 403

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE username = ?", (target_user,))
        cursor.execute("DELETE FROM results WHERE username = ?", (target_user,))
        cursor.execute("DELETE FROM user_progress WHERE username = ?", (target_user,))
        cursor.execute("DELETE FROM sector_completions WHERE username = ?", (target_user,))
        cursor.execute("DELETE FROM debug_progress WHERE username = ?", (target_user,))
        cursor.execute("DELETE FROM active_quizzes WHERE username = ?", (target_user,))
        conn.commit()

    self_delete = (current_user == target_user)
    if self_delete:
        session.clear()

    return jsonify({
        "success": True,
        "message": f"User '{target_user}' successfully deleted.",
        "self_delete": self_delete
    })

@app.route("/logout")
def logout():
    clear_active_quiz()
    session.clear()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="127.0.0.1", port=5000)
