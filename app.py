import os
import sqlite3
import hashlib
import hmac
import json
import csv
import io
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
DAILY_ATTEMPTS = defaultdict(deque)
USER_AVATARS = ("🐍", "⚙️", "💻", "🔐", "🚀")

def player_title(sectors_cleared, debug_xp):
    if sectors_cleared >= 3:
        return "Escape Artist"
    if debug_xp >= 700:
        return "Bug Hunter"
    if sectors_cleared >= 1:
        return "Sector Scout"
    return "Cadet"

def process_memory_mb():
    """Best-effort RSS read without making psutil a required dependency."""
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except ImportError:
        pass
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            counters = ProcessMemoryCounters(); counters.cb = ctypes.sizeof(counters)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
                return round(counters.WorkingSetSize / (1024 * 1024), 1)
        else:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return round(usage / (1024 if usage > 10**7 else 1), 1)
    except (ImportError, AttributeError, OSError, ValueError):
        return None
    return None

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
# MAIN QUIZ QUESTION BANK (10 questions per run, rotated through 30)
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

# Each sector has a 30-question pool. Topics are deliberately distinct so a
# single run samples different kinds of reasoning instead of repeating a pattern.
_EXTRA_QUESTIONS = {
    "python": [
        ("Short-circuit", "What is printed?", "ready = True\nprint(ready and 'go')", ["True", "go", "ready", "Error"], 1, "and returns the second operand when the first is truthy.", "Because ready is True, the expression evaluates to and returns 'go'."),
        ("List aliasing", "What does the final print show?", "left = [1, 2]\nright = left\nright.append(3)\nprint(left)", ["[1, 2]", "[1, 2, 3]", "[3]", "Error"], 1, "Both names refer to the same list object.", "Appending through right changes the shared list, so left also shows [1, 2, 3]."),
        ("Dictionary fallback", "What is printed when the key is missing?", "room = {'east': 4}\nprint(room.get('west', 0) + room['east'])", ["0", "4", "KeyError", "None"], 1, "get() returns its second argument when the key is absent.", "The missing west key gives 0, then 0 + 4 is 4."),
        ("Enumerate", "Which pair is printed on the second loop?", "for i, name in enumerate(['Ada', 'Lin']):\n    print(i, name)", ["0 Lin", "1 Lin", "2 Lin", "1 Ada"], 1, "enumerate starts counting at zero by default.", "The second item has index 1 and value Lin."),
        ("Nested loops", "How many times does 'x' print?", "for row in range(2):\n    for col in range(3):\n        print('x')", ["2", "3", "5", "6"], 3, "Multiply the number of outer and inner iterations.", "Two outer rounds each run the inner loop three times: 2 × 3 = 6."),
        ("Reverse slicing", "What does this slice produce?", "print('level'[::-1])", ["level", "le", "level[::-1]", "Error"], 0, "A step of -1 walks through the text backward.", "level reads the same in reverse, so it remains level."),
        ("Set uniqueness", "What length is printed?", "codes = {2, 2, 5, 7}\nprint(len(codes))", ["4", "3", "2", "1"], 1, "A set stores each distinct value once.", "The distinct values are 2, 5, and 7, so the length is 3."),
        ("Early return", "What does first_even return?", "def first_even(values):\n    for n in values:\n        if n % 2 == 0:\n            return n\n    return None\nprint(first_even([3, 7, 8, 10]))", ["3", "8", "10", "None"], 1, "return ends the function as soon as its condition is met.", "The first even value encountered is 8, and the function returns immediately."),
        ("Safe division", "What is printed?", "denominator = 0\nprint('skip' if denominator == 0 else 12 / denominator)", ["0", "12", "skip", "ZeroDivisionError"], 2, "Only the selected branch of a conditional expression runs.", "The condition is true, so Python selects 'skip' without dividing."),
        ("Range endpoint", "How many values are summed?", "print(sum(range(1, 5)))", ["4", "5", "10", "15"], 2, "range stops before its second argument.", "The values are 1, 2, 3, and 4; their sum is 10."),
        ("String rebinding", "What is printed?", "word = 'cat'\nword = word.upper()\nprint(word)", ["cat", "CAT", "Cat", "Error"], 1, "upper() returns an uppercase string, which is assigned back to word.", "word is rebound to the new string CAT."),
        ("Recursion", "What does countdown(3) return?", "def countdown(n):\n    if n == 0:\n        return 1\n    return n * countdown(n - 1)\nprint(countdown(3))", ["3", "6", "9", "0"], 1, "Expand the calls down to the base case at zero.", "The result is 3 × 2 × 1 × 1 = 6."),
        ("Sort key", "Which name comes first after sorting by length?", "names = ['Ravi', 'Bo', 'Mina']\nnames.sort(key=len)\nprint(names[0])", ["Ravi", "Bo", "Mina", "Mina and Ravi"], 1, "The key makes sort compare string lengths.", "Bo has length 2, shorter than the other names."),
        ("Zip", "What total is printed?", "a = [2, 4, 6]\nb = [1, 3]\nprint(sum(x + y for x, y in zip(a, b)))", ["9", "10", "16", "Error"], 0, "zip stops when the shorter list ends.", "It pairs (2,1) and (4,3), giving 3 + 7 = 10. (Correct answer updated below.)"),
        ("Exception flow", "Which word is printed?", "try:\n    int('4x')\nexcept ValueError:\n    print('retry')\nelse:\n    print('done')", ["retry", "done", "4x", "Nothing"], 0, "int() cannot parse a number containing x.", "ValueError is caught, so the except block prints retry."),
        ("Operator order", "What is printed?", "print(2 + 3 * 4 ** 2)", ["400", "56", "80", "196"], 1, "Exponentiation runs before multiplication, then addition.", "4 ** 2 = 16; 3 × 16 = 48; 2 + 48 = 50. (Correct answer updated below.)"),
        ("Filtered comprehension", "What list is printed?", "print([n * 2 for n in range(5) if n % 2])", ["[0, 2, 4, 6, 8]", "[2, 6]", "[1, 3]", "[2, 4, 6]"], 1, "The condition keeps odd n values before doubling.", "Odd values in range(5) are 1 and 3, which double to [2, 6]."),
        ("Stable tie", "Which value is returned?", "scores = [4, 9, 9, 2]\nprint(scores.index(max(scores)))", ["1", "2", "9", "0"], 0, "index() returns the first position matching the maximum.", "The maximum is 9 and its first occurrence is at index 1.")
    ],
    "c": [
        ("Short-circuit", "What is printed?", "int x = 0;\nif (x != 0 && 10 / x > 1) puts(\"yes\");\nelse puts(\"safe\");", ["yes", "safe", "0", "Division error"], 1, "C's && skips the right side when the left side is false.", "x != 0 is false, so division is skipped and safe is printed."),
        ("Do-while", "How many times does the body run?", "int n = 5, count = 0;\ndo { count++; } while (n < 3);\nprintf(\"%d\", count);", ["0", "1", "2", "Infinite"], 1, "A do-while executes its body before checking the condition.", "The body runs once, then n < 3 is false."),
        ("Switch break", "What is printed?", "int code = 2;\nswitch (code) { case 1: printf(\"A\"); break; case 2: printf(\"B\"); break; default: printf(\"C\"); }", ["A", "B", "C", "BC"], 1, "Match the case value and stop at its break.", "Case 2 prints B and break prevents fall-through."),
        ("Post-increment", "What is the behavior of this call?", "int n = 4;\nprintf(\"%d %d\", n++, n);", ["Always prints 4 5", "Always prints 5 5", "Undefined behavior", "Compile error"], 2, "The arguments may be evaluated in either order, but there is a deeper issue.", "One argument modifies n while another reads it without a sequencing rule between them, which is undefined behavior in C."),
        ("Pass by value", "What does main print?", "void change(int n) { n = 99; }\nint main(void) { int value = 5; change(value); printf(\"%d\", value); }", ["99", "5", "0", "Compile error"], 1, "The function receives a copy of the integer.", "Changing the parameter copy does not change value in main."),
        ("Pointer update", "What is printed?", "int value = 6;\nint *ptr = &value;\n*ptr += 2;\nprintf(\"%d\", value);", ["6", "8", "2", "Address"], 1, "*ptr accesses the value stored at the address.", "The pointed-to variable is increased by 2, so value becomes 8."),
        ("Character array size", "What does sizeof(word) count?", "char word[] = \"cat\";\nprintf(\"%zu\", sizeof(word));", ["3", "4", "1", "Depends on text"], 1, "A C string array also stores its terminating null character.", "The array holds c, a, t, and '\\0', so sizeof is 4 bytes."),
        ("Continue", "What is the sum?", "int total = 0;\nfor (int i = 1; i <= 4; i++) { if (i == 3) continue; total += i; }\nprintf(\"%d\", total);", ["10", "7", "6", "3"], 1, "continue skips the remaining body for i equal to 3.", "The included values are 1, 2, and 4, summing to 7."),
        ("Nested condition", "Which word is printed?", "int x = 8;\nif (x > 5) { if (x % 2 == 0) puts(\"even\"); else puts(\"odd\"); }", ["even", "odd", "8", "Nothing"], 0, "Check the outer condition, then the inner remainder test.", "8 is greater than 5 and divisible by 2, so even is printed."),
        ("Array boundary", "Which is the last valid index?", "int data[5] = {1,2,3,4,5};", ["5", "4", "6", "-1"], 1, "An array of five items starts at index zero.", "The valid indexes are 0 through 4."),
        ("Struct field", "What is printed?", "struct Point { int x; int y; };\nstruct Point p = {3, 7};\nprintf(\"%d\", p.y - p.x);", ["4", "10", "-4", "37"], 0, "The initializer fills x first, then y.", "p.y is 7 and p.x is 3, so their difference is 4."),
        ("Return value", "What does the call print?", "int clamp(int n) { if (n < 0) return 0; if (n > 9) return 9; return n; }\nprintf(\"%d\", clamp(12));", ["12", "9", "0", "-1"], 1, "Trace the first condition that returns from the function.", "12 is greater than 9, so clamp returns 9."),
        ("While trace", "What is the final value of n?", "int n = 1;\nwhile (n < 10) n *= 2;\nprintf(\"%d\", n);", ["8", "10", "16", "32"], 2, "Double until n is no longer less than 10.", "The values are 1, 2, 4, 8, then 16; at 16 the condition is false."),
        ("Logical precedence", "What does this expression evaluate to?", "printf(\"%d\", 1 || 0 && 0);", ["0", "1", "2", "Error"], 1, "&& has higher precedence than ||.", "0 && 0 is 0, then 1 || 0 is 1."),
        ("Comma operator", "What is printed?", "int a = 1, b = 2;\nint result = (a += 2, b += 3);\nprintf(\"%d %d %d\", a, b, result);", ["3 5 5", "1 2 3", "3 2 3", "5 3 5"], 0, "The comma operator evaluates left to right and yields its final expression.", "a becomes 3, b becomes 5, and the expression value is b's new value, 5."),
        ("Function argument", "What is printed?", "int twice(int n) { return n * 2; }\nprintf(\"%d\", twice(3 + 2));", ["7", "10", "12", "32"], 1, "Evaluate the argument before entering the function.", "3 + 2 is 5; twice returns 5 × 2 = 10."),
        ("Bit mask", "What is the result?", "printf(\"%d\", 6 & 3);", ["2", "3", "5", "6"], 0, "Write both values in binary and keep bits set in both.", "6 is 110 and 3 is 011; AND gives 010, which is 2."),
        ("Array sum", "What total is printed?", "int v[] = {2, 5, 8};\nint total = 0;\nfor (int i = 0; i < 3; i++) total += v[i] - i;\nprintf(\"%d\", total);", ["15", "12", "9", "8"], 1, "Subtract the current index from each element before adding.", "The terms are 2, 4, and 6, giving 12.")
    ],
    "logic": [
        ("Truth condition", "A lamp turns on only when A is on and B is off. A is on; B is on. What happens?", "", ["Lamp on", "Lamp off", "Cannot tell", "Both states"], 1, "The rule requires both parts to be true.", "A is on, but B is not off, so the combined condition fails."),
        ("Changing gaps", "What comes next: 3, 6, 11, 18, 27, ?", "", ["36", "38", "40", "42"], 1, "Compare the differences between neighboring terms.", "The gaps are 3, 5, 7, 9; the next gap is 11, giving 38."),
        ("Ordering constraints", "Mina arrives before Jo. Lee arrives after Jo. Who arrives last?", "", ["Mina", "Jo", "Lee", "Cannot tell"], 2, "Turn the clues into one ordered line.", "Mina is before Jo, and Lee is after Jo: Mina, Jo, Lee."),
        ("Pair counting", "Four teams each play every other team once. How many matches are played?", "", ["4", "6", "8", "12"], 1, "Each match is one unique pair of teams.", "There are 4 × 3 / 2 = 6 unique pairs."),
        ("Two-step chance", "A bag has 2 red and 2 blue tokens. You draw one, replace it, then draw again. Chance both are red?", "", ["1/2", "1/4", "1/8", "3/4"], 1, "The two independent draw probabilities multiply.", "Each draw has a 1/2 chance of red, so 1/2 × 1/2 = 1/4."),
        ("Weekday shift", "If the 1st is a Tuesday, what weekday is the 18th?", "", ["Wednesday", "Thursday", "Friday", "Saturday"], 1, "The 18th is 17 days after the 1st; reduce by full weeks.", "17 days is 2 weeks and 3 days. Tuesday plus 3 days is Friday. (Correct answer updated below.)"),
        ("Work rate", "A printer makes 30 pages in 5 minutes. At the same rate, how long for 54 pages?", "", ["7 minutes", "8 minutes", "9 minutes", "10 minutes"], 2, "Find pages per minute, then divide 54 by that rate.", "The rate is 6 pages per minute; 54 ÷ 6 = 9 minutes."),
        ("Overlap", "In a group of 20, 12 like chess and 11 like coding. At least how many like both?", "", ["0", "1", "3", "11"], 1, "If the two groups totaled more than 20 without overlap, someone would be counted twice.", "12 + 11 − 20 = 3 must like both. (Correct answer updated below.)"),
        ("Grid movement", "You move 3 blocks east and 2 west. How far east of the start are you?", "", ["1 block", "5 blocks", "2 blocks", "At the start"], 0, "Opposite directions subtract.", "Three east minus two west leaves one block east."),
        ("Minimum coins", "Using 1, 3, and 4 point tokens, what is the fewest tokens to make 6?", "", ["2", "3", "4", "6"], 0, "Check whether a larger token can combine with another available token.", "3 + 3 makes 6 using two tokens; one token cannot make 6."),
        ("Truth deduction", "Every red key opens a gate. Key K opened no gate. What follows?", "", ["K is red", "K is not red", "K is broken", "K opens every gate"], 1, "Use the rule in reverse: a red key must open a gate.", "Since K opened no gate, it cannot be red under the stated rule."),
        ("Consecutive numbers", "Three consecutive whole numbers sum to 36. What is the middle number?", "", ["11", "12", "13", "14"], 1, "The middle of three consecutive numbers is also their average.", "36 ÷ 3 = 12, so the numbers are 11, 12, and 13."),
        ("Clock angle", "At 3:00, what is the smaller angle between the clock hands?", "", ["45°", "60°", "90°", "180°"], 2, "Each of the 12 hour spaces is 30 degrees.", "At 3:00 the hands are three spaces apart: 3 × 30° = 90°."),
        ("Balance scale", "A box weighs as much as 2 kg plus half the box. What does the box weigh?", "", ["2 kg", "3 kg", "4 kg", "6 kg"], 2, "Subtract half the box's weight from both sides.", "Half the box weighs 2 kg, so the full box weighs 4 kg."),
        ("Set difference", "All 8 students joined art or music. 5 joined art and 4 joined music. How many joined both?", "", ["0", "1", "2", "4"], 1, "Use total = art + music − overlap.", "8 = 5 + 4 − overlap, so the overlap is 1."),
        ("Conditional logic", "If the alarm sounds, the door locks. The door is not locked. What can you conclude?", "", ["Alarm sounded", "Alarm did not sound", "Door is open", "Cannot infer anything"], 1, "If A guarantees B, then not B rules out A.", "If the alarm had sounded, the door would be locked. Since it is not, the alarm did not sound."),
        ("Number pattern", "What comes next: 1, 2, 4, 7, 11, ?", "", ["15", "16", "17", "18"], 1, "The added amounts increase by one each time.", "The increments are 1, 2, 3, 4; next add 5 to get 16."),
        ("Route choice", "A path goes north 4 km, east 3 km, then south 4 km. Where are you relative to start?", "", ["3 km east", "4 km north", "7 km east", "At the start"], 0, "North and south distances cancel.", "The 4 km north and 4 km south cancel, leaving 3 km east.")
    ]
}
for _level, _items in _EXTRA_QUESTIONS.items():
    _bank = QUESTION_BANK[_level]
    for _offset, (_topic, _text, _code, _opts, _correct, _hint, _exp) in enumerate(_items, start=len(_bank) + 1):
        _bank.append({"id": f"{_level}-{_offset:03d}", "topic": _topic, "difficulty": "Core", "text": _text,
                      "code": _code, "opts": _opts, "correct": _correct, "hint": _hint, "exp": _exp})
# Correct the explicitly noted arithmetic distractors and date arithmetic above.
for _question in QUESTION_BANK["python"]:
    if _question["topic"] == "Zip":
        _question["opts"] = ["10", "9", "16", "Error"]
        _question["correct"] = 0
    elif _question["topic"] == "Operator order":
        _question["opts"] = ["400", "50", "80", "196"]
        _question["correct"] = 1
for _question in QUESTION_BANK["logic"]:
    if _question["topic"] == "Weekday shift":
        _question["opts"] = ["Wednesday", "Thursday", "Friday", "Saturday"]
        _question["correct"] = 2
    elif _question["topic"] == "Overlap":
        _question["opts"] = ["0", "1", "3", "11"]
        _question["correct"] = 2
for _level, _bank in QUESTION_BANK.items():
    for _index, _question in enumerate(_bank, start=1):
        _question["id"] = f"{_level}-{_index:03d}"
        if _level == "logic" and _index == 6:
            _question["topic"] = "Multiply-add sequence"
    assert len(_bank) == 30 and len({q["topic"] for q in _bank}) == 30

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

# PvP rotates through these reasoning-focused Python tasks alongside the
# introductory debugging exercises above.
_ARENA_CHALLENGES = [
    ("py009", "BUG #09 — Keep the First Occurrence", "Fix the loop so it prints each number once, preserving its first appearance.", "values = [4, 2, 4, 3, 2]\nunique = []\nfor value in values:\n    if value in unique:\n        unique.append(value)\nprint(unique)", "[4, 2, 3]", "Check whether a value has already been collected before appending it.", "Change the condition to if value not in unique."),
    ("py010", "BUG #10 — Find the Best Score", "Print the largest score without changing the original list.", "scores = [12, 19, 7, 15]\nbest = scores[0]\nfor score in scores:\n    if score < best:\n        best = score\nprint(best)", "19", "The comparison currently replaces best with smaller values.", "Replace score < best with score > best."),
    ("py011", "BUG #11 — Count Only Valid Entries", "Count values from 1 through 5 inclusive. The list contains [0, 1, 5, 6]; print 2.", "values = [0, 1, 5, 6]\ncount = 0\nfor value in values:\n    if value >= 1 and value < 5:\n        count += 1\nprint(count)", "2", "The upper edge is included in the task.", "Use value <= 5 for the upper bound."),
    ("py012", "BUG #12 — Running Balance", "Apply each change to the balance. Starting at 10, changes [-3, 5, -2] should leave 10.", "balance = 10\nfor change in [-3, 5, -2]:\n    balance = change\nprint(balance)", "10", "The loop should update the existing balance, not replace it.", "Use balance += change."),
    ("py013", "BUG #13 — Track the Longest Word", "Print the longest word in the list. If lengths tie, keep the first one.", "words = ['map', 'vault', 'code']\nbest = words[0]\nfor word in words:\n    if len(word) < len(best):\n        best = word\nprint(best)", "vault", "The current comparison replaces best with shorter words.", "Use len(word) > len(best) to keep a longer word."),
    ("py014", "BUG #14 — Pair the Matching Index", "Print the sum of values at even indexes. For [5, 8, 2, 9, 4], print 11.", "values = [5, 8, 2, 9, 4]\ntotal = 0\nfor index in range(len(values)):\n    if index % 2 == 1:\n        total += values[index]\nprint(total)", "11", "Indexes start at zero; the even indexes are 0, 2, and 4.", "Change the remainder check to index % 2 == 0."),
]
for _cid, _title, _description, _starter, _output, _hint, _fix in _ARENA_CHALLENGES:
    DEBUG_CHALLENGES["python"].append({"id":_cid, "title":_title, "difficulty":3, "description":_description,
        "starterCode":_starter, "expectedOutput":_output, "hints":[_hint, _fix], "xp":250})
    _fix_code = {
        "py009":"values = [4, 2, 4, 3, 2]\nunique = []\nfor value in values:\n    if value not in unique:\n        unique.append(value)\nprint(unique)",
        "py010":"scores = [12, 19, 7, 15]\nbest = scores[0]\nfor score in scores:\n    if score > best:\n        best = score\nprint(best)",
        "py011":"values = [0, 1, 5, 6]\ncount = 0\nfor value in values:\n    if value >= 1 and value <= 5:\n        count += 1\nprint(count)",
        "py012":"balance = 10\nfor change in [-3, 5, -2]:\n    balance += change\nprint(balance)",
        "py013":"words = ['map', 'vault', 'code']\nbest = words[0]\nfor word in words:\n    if len(word) > len(best):\n        best = word\nprint(best)",
        "py014":"values = [5, 8, 2, 9, 4]\ntotal = 0\nfor index in range(len(values)):\n    if index % 2 == 0:\n        total += values[index]\nprint(total)"
    }[_cid]
    DEBUG_SOLUTIONS[_cid] = _fix_code

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
                avatar TEXT NOT NULL DEFAULT '🐍',
                is_banned INTEGER NOT NULL DEFAULT 0,
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
        solved_state_existed = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'debug_solved_state'"
        ).fetchone() is not None
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS debug_solved_state (
                username TEXT NOT NULL,
                challenge_id TEXT NOT NULL,
                solved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (username, challenge_id)
            )
        """)
        if not solved_state_existed:
            cursor.execute("""
                INSERT OR IGNORE INTO debug_solved_state (username, challenge_id)
                SELECT username, challenge_id FROM debug_progress
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
            CREATE TABLE IF NOT EXISTS daily_challenge_progress (
                username TEXT NOT NULL,
                challenge_date TEXT NOT NULL,
                challenge_id TEXT NOT NULL,
                xp INTEGER NOT NULL DEFAULT 0,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (username, challenge_date)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS quiz_question_rotation (
                username TEXT NOT NULL,
                level TEXT NOT NULL,
                deck_json TEXT NOT NULL,
                cursor INTEGER NOT NULL DEFAULT 0,
                last_batch_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (username, level)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS community_puzzles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                title TEXT NOT NULL,
                language TEXT NOT NULL CHECK(language IN ('python','c')),
                description TEXT NOT NULL,
                starter_code TEXT NOT NULL,
                expected_output TEXT NOT NULL,
                difficulty TEXT NOT NULL DEFAULT 'Core',
                status TEXT NOT NULL DEFAULT 'pending',
                votes INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        puzzle_columns={row["name"] for row in cursor.execute("PRAGMA table_info(community_puzzles)").fetchall()}
        if "difficulty" not in puzzle_columns:cursor.execute("ALTER TABLE community_puzzles ADD COLUMN difficulty TEXT NOT NULL DEFAULT 'Core'")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS community_votes (
                puzzle_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(puzzle_id, username)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS arena_queue (
                username TEXT PRIMARY KEY,
                match_id TEXT,
                challenge_id TEXT NOT NULL,
                queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS arena_matches (
                match_id TEXT PRIMARY KEY,
                player_one TEXT NOT NULL,
                player_two TEXT NOT NULL,
                challenge_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'active',
                progress_one TEXT NOT NULL DEFAULT '{}',
                progress_two TEXT NOT NULL DEFAULT '{}',
                glitch_until REAL NOT NULL DEFAULT 0,
                hack_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS site_announcements (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                message TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_notification_state (
                username TEXT PRIMARY KEY,
                last_feedback_id INTEGER NOT NULL DEFAULT 0,
                last_reply_id INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        if "avatar" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN avatar TEXT NOT NULL DEFAULT '🐍'")
        if "is_banned" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0")
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
        # Never ship administrator names or PINs in source. A new administrator can
        # be bootstrapped explicitly through environment variables on first setup.
        bootstrap_name = os.environ.get("CAMPUS_ESCAPE_ADMIN_USERNAME", "").strip()
        bootstrap_pin = os.environ.get("CAMPUS_ESCAPE_ADMIN_PIN", "").strip()
        if bootstrap_name and bootstrap_pin:
            if not re.fullmatch(r"[0-9]{4}", bootstrap_pin):
                raise RuntimeError("CAMPUS_ESCAPE_ADMIN_PIN must be exactly four digits.")
            admin = cursor.execute(
                "SELECT id, role FROM users WHERE username = ? COLLATE NOCASE", (bootstrap_name,)
            ).fetchone()
            if admin is None:
                cursor.execute(
                    "INSERT INTO users (username, player_id, pin_hash, role) VALUES (?, ?, ?, 'admin')",
                    (bootstrap_name, generate_player_id(cursor), generate_password_hash(bootstrap_pin, method="pbkdf2:sha256", salt_length=16)),
                )
            elif admin["role"] != "admin":
                raise RuntimeError(
                    "The configured admin username already belongs to a player. Choose a new username."
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

@app.context_processor
def inject_global_player_hud():
    """Expose only safe player summary fields to the shared navigation shell."""
    username = session.get("username")
    if not username:
        return {"shell_player": None}
    with get_db() as conn:
        player = conn.execute(
            "SELECT player_id, role, best_score, avatar FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if not player:
            return {"shell_player": None}
        xp_row = conn.execute(
            "SELECT (SELECT COALESCE(SUM(xp),0) FROM debug_progress WHERE username=?) + "
            "(SELECT COALESCE(SUM(xp),0) FROM daily_challenge_progress WHERE username=?) AS xp",
            (username, username),
        ).fetchone()
        clears = conn.execute(
            "SELECT COUNT(*) FROM sector_completions WHERE username = ?", (username,)
        ).fetchone()[0]
        completed_levels = {
            row["level"] for row in conn.execute(
                "SELECT level FROM sector_completions WHERE username = ?", (username,)
            ).fetchall()
        }
        vault_row = conn.execute(
            "SELECT vault_cleared FROM user_progress WHERE username = ?", (username,)
        ).fetchone()
        admin_notifications = 0
        if player["role"] == "admin":
            seen = conn.execute(
                "SELECT last_feedback_id, last_reply_id FROM admin_notification_state WHERE username = ?",
                (username,),
            ).fetchone()
            if seen:
                admin_notifications = conn.execute("""
                    SELECT
                      (SELECT COUNT(*) FROM feedback f
                       WHERE f.id > ? AND f.channel IN ('admin','community') AND f.hidden = 0)
                      +
                      (SELECT COUNT(*) FROM feedback_replies r
                       JOIN feedback f ON f.id = r.feedback_id
                       WHERE r.id > ? AND r.username != ? AND r.hidden = 0 AND f.hidden = 0
                         AND f.channel IN ('admin','community'))
                """, (seen["last_feedback_id"], seen["last_reply_id"], username)).fetchone()[0]
            else:
                # Treat messages that existed before this notification feature was
                # first used as already seen; only future activity should alert.
                latest_feedback = conn.execute("SELECT COALESCE(MAX(id), 0) FROM feedback").fetchone()[0]
                latest_reply = conn.execute("SELECT COALESCE(MAX(id), 0) FROM feedback_replies").fetchone()[0]
                conn.execute(
                    "INSERT INTO admin_notification_state(username, last_feedback_id, last_reply_id) VALUES (?, ?, ?)",
                    (username, latest_feedback, latest_reply),
                )
                conn.commit()
    xp = int(xp_row["xp"] if xp_row else 0)
    return {"shell_player": {
        "username": username,
        "player_id": player["player_id"] or "—",
        "role": player["role"],
        "score": int(player["best_score"] or 0),
        "xp": xp,
        "rank": player_title(clears, xp),
        "python_complete": "python" in completed_levels,
        "c_complete": "c" in completed_levels,
        "logic_complete": "logic" in completed_levels,
        "vault_cleared": bool(vault_row and vault_row["vault_cleared"]),
    }, "shell_admin_notifications": min(int(admin_notifications), 99)}

@app.before_request
def block_banned_accounts():
    username=session.get("username")
    if not username or request.endpoint in ("static", "logout"):
        return None
    with get_db() as conn:
        row=conn.execute("SELECT is_banned FROM users WHERE username=?",(username,)).fetchone()
    if row and row["is_banned"]:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"success":False,"message":"This account is suspended. Contact the administrator."}),403
        return redirect(url_for("index"))
    return None

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
                   ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
                   ast.In: lambda left, right: left in right,
                   ast.NotIn: lambda left, right: left not in right}

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
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.attr == "append"
                and not node.keywords and len(node.args) == 1):
            target = self.env.get(node.func.value.id)
            if not isinstance(target, list):
                raise ValueError("append() is supported only for a named list.")
            if len(target) >= 10000:
                raise ValueError("List limit reached (10,000 items).")
            target.append(self.expression(node.args[0]))
            return None
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
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return render_template("index.html", csrf_token=csrf_token)
@app.route("/map")
def map_page():
    if "username" not in session:
        return redirect(url_for("index"))

    with get_db() as conn:
        cursor = conn.cursor()
        account = cursor.execute("SELECT player_id, role, avatar FROM users WHERE username = ?", (session["username"],)).fetchone()
        cursor.execute("""
            SELECT MAX(python_score) as py_s, MAX(c_score) as c_s, MAX(logic_score) as log_s
            FROM results WHERE username = ?
        """, (session["username"],))
        res = cursor.fetchone()
        map_xp_row = cursor.execute("SELECT (SELECT COALESCE(SUM(xp),0) FROM debug_progress WHERE username=?) + (SELECT COALESCE(SUM(xp),0) FROM daily_challenge_progress WHERE username=?) AS xp", (session["username"], session["username"])).fetchone()

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
        player_title=player_title(sum((python_completed, c_completed, logic_completed)), map_xp_row["xp"] if map_xp_row else 0),
        player_id=account["player_id"] if account else "—",
        avatar=account["avatar"] if account else "🐍",
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
        account = cursor.execute("SELECT player_id, avatar FROM users WHERE username = ?", (session["username"],)).fetchone()
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
        player_id=account["player_id"] if account else "",
        avatar=account["avatar"] if account else "🐍",
        accuracy=accuracy,
        date=today_date,
        certificate_id=certificate_id,
        python_score=py_s,
        c_score=c_s,
        logic_score=log_s,
        total_score=py_s + c_s + log_s,
        debug_xp=debug_xp_row["xp"] if debug_xp_row else 0,
        badges=[label for label, unlocked in (("Bug Hunter", (debug_xp_row["xp"] if debug_xp_row else 0) >= 700), ("Escape Artist", all_sectors_completed(session["username"])), ("Master Cipher", vault_is_cleared(session["username"]))) if unlocked],
        admin_preview=admin_preview,
    )

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("index"))

    with get_db() as conn:
        user_info = conn.execute(
            "SELECT player_id, best_score, attempts, role, avatar FROM users WHERE username = ?",
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
        daily_row = conn.execute("SELECT COUNT(*) AS clears, COALESCE(SUM(xp),0) AS xp FROM daily_challenge_progress WHERE username = ?", (session["username"],)).fetchone()
        rank_rows = conn.execute("""
            SELECT u.username, u.best_score,
                   (SELECT COUNT(*) FROM sector_completions s WHERE s.username=u.username) AS sectors,
                   (SELECT COALESCE(SUM(xp),0) FROM debug_progress d WHERE d.username=u.username) +
                   (SELECT COALESCE(SUM(xp),0) FROM daily_challenge_progress dc WHERE dc.username=u.username) AS xp
            FROM users u WHERE u.role='player'
            ORDER BY u.best_score DESC, sectors DESC, xp DESC, u.created_at ASC
        """).fetchall()
    debug_xp = (debug_xp_row["xp"] if debug_xp_row else 0) + (daily_row["xp"] if daily_row else 0)
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
        username=session["username"],
        player_id=user_info["player_id"],
        avatar=user_info["avatar"],
        player_title=player_title(sum(1 for sector in sectors if sector["cleared"]), debug_xp),
        is_admin=user_info["role"] == "admin",
        best_score=best_score,
        attempts=attempts,
        py_best=py_best,
        c_best=c_best,
        logic_best=logic_best,
        latest=latest,
        history=history,
        achievements=achievements,
        debug_xp=debug_xp,
        daily_clears=daily_row["clears"] if daily_row else 0,
        sectors=sectors,
        vault_cleared=vault_is_cleared(session["username"]),
        accuracy=latest["accuracy"] if latest and latest["accuracy"] is not None else 0,
        leaderboard_rank=next((index for index, row in enumerate(rank_rows, start=1) if row["username"] == session["username"]), None),
    )

def daily_question_for(date_key):
    day_number = datetime.date.fromisoformat(date_key).toordinal()
    mode_day = day_number % 3
    if mode_day == 0:
        puzzles = [
            {"id":"parson-sum-evens", "topic":"Code reconstruction", "text":"Arrange the blocks to create sum_even(values), which returns the sum of only the even numbers. For [3, 4, 8], the result should be 12.", "blocks":["def sum_even(values):", "    total = 0", "    for value in values:", "        if value % 2 == 0:", "            total += value", "    return total"]},
            {"id":"parson-count-positive", "topic":"Code reconstruction", "text":"Arrange the blocks to define count_positive(values) and return how many values are greater than zero. For [-2, 5, 1], the result should be 2.", "blocks":["def count_positive(values):", "    count = 0", "    for value in values:", "        if value > 0:", "            count += 1", "    return count"]},
            {"id":"parson-first-long", "topic":"Code reconstruction", "text":"Arrange the blocks to define first_long(words), returning the first word longer than 4 characters, or None if there is no match.", "blocks":["def first_long(words):", "    for word in words:", "        if len(word) > 4:", "            return word", "    return None"]},
            {"id":"parson-running-max", "topic":"Code reconstruction", "text":"Arrange the blocks to define running_total(values), returning a list of cumulative sums. For [2, 3, 1], return [2, 5, 6].", "blocks":["def running_total(values):", "    result = []", "    total = 0", "    for value in values:", "        total += value", "        result.append(total)", "    return result"]},
            {"id":"parson-count-vowels", "topic":"Code reconstruction", "text":"Arrange the blocks to define count_vowels(text), counting a, e, i, o, and u regardless of case.", "blocks":["def count_vowels(text):", "    vowels = 'aeiou'", "    count = 0", "    for character in text.lower():", "        if character in vowels:", "            count += 1", "    return count"]},
            {"id":"parson-unique-order", "topic":"Code reconstruction", "text":"Arrange the blocks to define unique_in_order(values), keeping each value's first appearance. For [2, 2, 5, 2], return [2, 5].", "blocks":["def unique_in_order(values):", "    result = []", "    for value in values:", "        if value not in result:", "            result.append(value)", "    return result"]}
        ]
        puzzle = random.Random(f"campus-escape-parson:{date_key}").choice(puzzles)
        order = list(range(len(puzzle["blocks"])))
        random.Random(f"campus-escape-parson-order:{date_key}").shuffle(order)
        return "python", {**puzzle, "mode":"parson", "correct_order":list(range(len(order))),
                           "blocks":[{"id":i, "text":puzzle["blocks"][i]} for i in order]}
    if mode_day == 1:
        puzzles = [
            {"id":"debug-running-total", "topic":"Debug the code", "text":"Fix the update so every change is added to the current balance. The final balance should be 10.", "starterCode":"balance = 10\nfor change in [-3, 5, -2]:\n    balance = change\nprint(balance)", "expectedOutput":"10", "hint":"The loop currently replaces the balance with each new change."},
            {"id":"debug-inclusive-range", "topic":"Debug the code", "text":"Fix the loop so it adds all values from 1 through 4 inclusive. The total should be 10.", "starterCode":"total = 0\nfor value in range(1, 4):\n    total += value\nprint(total)", "expectedOutput":"10", "hint":"Python's range stops before its final number."},
            {"id":"debug-best-score", "topic":"Debug the code", "text":"Fix the comparison so best keeps the highest score. The program should print 19.", "starterCode":"scores = [12, 19, 7, 15]\nbest = scores[0]\nfor score in scores:\n    if score < best:\n        best = score\nprint(best)", "expectedOutput":"19", "hint":"The current comparison replaces best when it finds a smaller value."},
            {"id":"debug-count-positives", "topic":"Debug the code", "text":"Fix the condition to count values above zero. For [-2, 5, 1, 0], print 2.", "starterCode":"values = [-2, 5, 1, 0]\ncount = 0\nfor value in values:\n    if value < 0:\n        count += 1\nprint(count)", "expectedOutput":"2", "hint":"Zero is not positive, and negative values should not be counted."}
        ]
        puzzle = random.Random(f"campus-escape-debug:{date_key}").choice(puzzles)
        return "python", {**puzzle, "mode":"debug"}
    candidates = [(level, q) for level, bank in QUESTION_BANK.items()
                  for q in bank if q["difficulty"].lower() == "challenge"]
    rng = random.Random(f"campus-escape:{date_key}")
    level, question = rng.choice(candidates)
    return level, {**question, "mode":"multiple_choice"}

def daily_cycle_key(now=None):
    """Daily missions roll over at 4:00 a.m. India Standard Time (UTC+05:30)."""
    india_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    current = now or datetime.datetime.now(india_tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=india_tz)
    current = current.astimezone(india_tz)
    if current.hour < 4:
        current -= datetime.timedelta(days=1)
    return current.date().isoformat()

@app.route("/daily-challenge")
def daily_challenge_page():
    if "username" not in session:
        return redirect(url_for("index"))
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return render_template("daily_challenge.html", csrf_token=csrf_token)

@app.route("/api/daily/get")
def daily_challenge_get():
    if "username" not in session:
        return jsonify({"success": False, "message": "Sign in to play the daily challenge."}), 401
    today = daily_cycle_key()
    level, question = daily_question_for(today)
    with get_db() as conn:
        done = conn.execute("SELECT xp, completed_at FROM daily_challenge_progress WHERE username = ? AND challenge_date = ?",
                            (session["username"], today)).fetchone()
    if question["mode"] == "parson":
        session["daily_active"] = {"date":today, "id":question["id"], "mode":"parson", "correct_order":question["correct_order"]}
        challenge = {"id":question["id"], "mode":"parson", "topic":question["topic"], "text":question["text"], "blocks":question["blocks"]}
    elif question["mode"] == "debug":
        session["daily_active"] = {"date":today, "id":question["id"], "mode":"debug"}
        challenge = {key:question[key] for key in ("id", "mode", "topic", "text", "starterCode", "expectedOutput", "hint")}
    else:
        option_pairs = list(enumerate(question["opts"]))
        random.shuffle(option_pairs)
        correct_index = next(i for i, pair in enumerate(option_pairs) if pair[0] == question["correct"])
        session["daily_active"] = {"date": today, "id": f"{level}-{question['id']}", "mode":"multiple_choice", "correct": correct_index}
        challenge = {"id": f"{level}-{question['id']}", "mode":"multiple_choice", "topic": question["topic"], "text": question["text"],
                     "code": question["code"], "opts": [p[1] for p in option_pairs]}
    return jsonify({"success": True, "date": today, "completed": bool(done), "xp": done["xp"] if done else 0,
                    "challenge": challenge})

@app.route("/api/daily/answer", methods=["POST"])
def daily_challenge_answer():
    username = session.get("username")
    active = session.get("daily_active", {})
    today = daily_cycle_key()
    if not username:
        return jsonify({"success": False, "message": "Sign in to play the daily challenge."}), 401
    expected_csrf = session.get("csrf_token", "")
    supplied_csrf = request.headers.get("X-CSRF-Token", "")
    if not expected_csrf or not secrets.compare_digest(expected_csrf, supplied_csrf):
        return jsonify({"success": False, "message": "Session check failed. Refresh the page and try again."}), 403
    if active.get("date") != today:
        return jsonify({"success": False, "message": "Reload today's challenge and try again."}), 400
    if rate_limited(DAILY_ATTEMPTS, username, 30, 300):
        return jsonify({"success": False, "message": "Too many attempts. Please wait a few minutes."}), 429
    payload = json_data()
    if active.get("mode") == "parson":
        selected = payload.get("order")
        if not isinstance(selected, list) or len(selected) != len(active.get("correct_order", [])) or any(type(i) is not int for i in selected) or sorted(selected) != list(range(len(selected))):
            return jsonify({"success": False, "message": "Arrange every code block once, then check your order."}), 400
        is_correct = selected == active["correct_order"]
    elif active.get("mode") == "debug":
        code = payload.get("code", "")
        _, daily_entry = daily_question_for(today)
        challenge = daily_entry if daily_entry.get("mode") == "debug" and daily_entry.get("id") == active.get("id") else None
        if not isinstance(code, str) or len(code) > 12000:
            return jsonify({"success":False, "message":"Enter code up to 12,000 characters."}), 400
        result = run_python_code(code)
        is_correct = result["success"] and result["stdout"].strip() == challenge["expectedOutput"].strip() if challenge else False
        if not is_correct:
            detail = result["stderr"] if not result["success"] else "The output does not match the target yet."
            return jsonify({"success":True, "correct":False, "message":detail or "Check your code and try again."})
    else:
        selected = payload.get("answer")
        if type(selected) is not int or not 0 <= selected < 4:
            return jsonify({"success": False, "message": "Choose one of the four answers."}), 400
        is_correct = selected == active.get("correct")
    if not is_correct:
        return jsonify({"success": True, "correct": False, "message": "Not quite. Review the clue and try again."})
    with get_db() as conn:
        cursor = conn.execute("INSERT OR IGNORE INTO daily_challenge_progress (username, challenge_date, challenge_id, xp) VALUES (?, ?, ?, 150)",
                              (username, today, active["id"]))
        conn.commit()
        awarded = cursor.rowcount == 1
    return jsonify({"success": True, "correct": True, "completed": True, "xp": 150 if awarded else 0,
                    "message": "Daily challenge cleared! Bonus XP saved." if awarded else "You already cleared today's challenge."})

@app.route("/leaderboard")
def leaderboard():
    username = session.get("username")
    with get_db() as conn:
        rows = conn.execute("""
            SELECT u.username, u.player_id, u.avatar, u.best_score,
                   (SELECT COUNT(*) FROM sector_completions s WHERE s.username=u.username) AS sectors,
                   (SELECT COALESCE(SUM(xp),0) FROM debug_progress d WHERE d.username=u.username) +
                   (SELECT COALESCE(SUM(xp),0) FROM daily_challenge_progress dc WHERE dc.username=u.username) AS xp
            FROM users u WHERE u.role='player'
            ORDER BY u.best_score DESC, sectors DESC, xp DESC, u.created_at ASC LIMIT 100
        """).fetchall()
    entries = [dict(row, rank=i + 1, is_current=(row["username"] == username)) for i, row in enumerate(rows)]
    return render_template("leaderboard.html", entries=entries, username=username)

@app.route("/challenge/<challenge_id>")
def shared_challenge(challenge_id):
    challenge = next((c for group in DEBUG_CHALLENGES.values() for c in group if c["id"] == challenge_id), None)
    if not challenge:
        abort(404)
    # Challenge pages show code/output but use the existing restricted runner.
    language = "c" if challenge["id"].startswith("c") else "python"
    return render_template("challenge.html", challenge=challenge, language=language, authenticated=("username" in session))

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
        analytics = conn.execute("""
            SELECT COUNT(*) AS players,
                   COALESCE(AVG((SELECT COALESCE(SUM(d.xp),0) FROM debug_progress d WHERE d.username=u.username) +
                                (SELECT COALESCE(SUM(dc.xp),0) FROM daily_challenge_progress dc WHERE dc.username=u.username)),0) AS avg_xp,
                   (SELECT COUNT(*) FROM sector_completions WHERE level='python') AS python_clears,
                   (SELECT COUNT(*) FROM sector_completions WHERE level='c') AS c_clears,
                   (SELECT COUNT(*) FROM sector_completions WHERE level='logic') AS logic_clears
            FROM users u WHERE role='player'
        """).fetchone()
        active_sessions = conn.execute("SELECT COUNT(DISTINCT username) FROM active_quizzes WHERE updated_at >= datetime('now','-5 minutes')").fetchone()[0]
        accounts = conn.execute("""
            SELECT u.username, u.player_id, u.role, u.is_banned, u.best_score, u.attempts, u.created_at,
                   (SELECT COUNT(*) FROM sector_completions s WHERE s.username = u.username) AS sectors,
                   (SELECT COALESCE(SUM(d.xp), 0) FROM debug_progress d WHERE d.username = u.username) AS debug_xp,
                   (SELECT COALESCE(v.vault_cleared, 0) FROM user_progress v WHERE v.username = u.username) AS vault_cleared
            FROM users u ORDER BY u.created_at DESC, u.id DESC
        """).fetchall()
        pending_puzzles = conn.execute("SELECT id,username,title,language,created_at FROM community_puzzles WHERE status='pending' ORDER BY created_at").fetchall()
    completion_rates = {level: round((analytics[f"{level}_clears"] / analytics["players"] * 100), 1) if analytics["players"] else 0
                        for level in ("python", "c", "logic")}
    return render_template("admin.html", accounts=accounts, stats=stats, feedback_stats=feedback_stats,
                           analytics=analytics, completion_rates=completion_rates, active_sessions=active_sessions, pending_puzzles=pending_puzzles,
                           memory_usage=process_memory_mb(),
                           admin_username=session["username"], csrf_token=csrf_token)

@app.route("/api/admin/notifications")
def admin_notifications_api():
    if "username" not in session:
        return jsonify({"success": False, "message": "Sign in required."}), 401
    if not is_current_admin():
        return jsonify({"success": False, "message": "Administrator access required."}), 403
    username = session["username"]
    with get_db() as conn:
        seen = conn.execute(
            "SELECT last_feedback_id, last_reply_id FROM admin_notification_state WHERE username = ?",
            (username,),
        ).fetchone()
        count = 0
        if seen:
            count = conn.execute("""
                SELECT
                  (SELECT COUNT(*) FROM feedback f
                   WHERE f.id > ? AND f.channel IN ('admin','community') AND f.hidden = 0)
                  +
                  (SELECT COUNT(*) FROM feedback_replies r
                   JOIN feedback f ON f.id = r.feedback_id
                   WHERE r.id > ? AND r.username != ? AND r.hidden = 0 AND f.hidden = 0
                     AND f.channel IN ('admin','community'))
            """, (seen["last_feedback_id"], seen["last_reply_id"], username)).fetchone()[0]
    return jsonify({"success": True, "count": min(int(count), 99)})

@app.route("/admin/results.csv")
def admin_results_csv():
    if "username" not in session:
        return redirect(url_for("index"))
    if not is_current_admin():
        abort(403)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT r.played_at, r.username, u.player_id, r.level, r.run_score,
                   r.python_score, r.c_score, r.logic_score, r.total_score, r.accuracy
            FROM results r LEFT JOIN users u ON u.username=r.username
            ORDER BY r.id DESC
        """).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(("Played at", "Username", "Player ID", "Lab", "Run score", "Python best", "C best", "Logic best", "Total best", "Accuracy"))
    writer.writerows([tuple(row) for row in rows])
    return app.response_class("\ufeff" + output.getvalue(), mimetype="text/csv",
                              headers={"Content-Disposition": "attachment; filename=campus-escape-results.csv"})

def clear_player_progress(conn, username):
    """Clear game state without deleting the account or community activity."""
    for table in ("results", "user_progress", "sector_completions", "debug_progress", "debug_solved_state",
                  "active_quizzes", "daily_challenge_progress", "quiz_question_rotation"):
        conn.execute(f'DELETE FROM "{table}" WHERE username = ?', (username,))
    match_ids = [row[0] for row in conn.execute(
        "SELECT match_id FROM arena_matches WHERE player_one = ? OR player_two = ?", (username, username)
    ).fetchall()]
    for match_id in match_ids:
        conn.execute("DELETE FROM arena_queue WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM arena_queue WHERE username = ?", (username,))
    conn.execute("DELETE FROM arena_matches WHERE player_one = ? OR player_two = ?", (username, username))
    conn.execute("UPDATE users SET best_score = 0, attempts = 0 WHERE username = ?", (username,))

def delete_player_account(conn, username):
    """Remove an account and its private/user-owned records consistently."""
    puzzle_ids = [row[0] for row in conn.execute(
        "SELECT id FROM community_puzzles WHERE username = ?", (username,)
    ).fetchall()]
    for puzzle_id in puzzle_ids:
        conn.execute("DELETE FROM community_votes WHERE puzzle_id = ?", (puzzle_id,))
    conn.execute("DELETE FROM community_puzzles WHERE username = ?", (username,))
    conn.execute("DELETE FROM community_votes WHERE username = ?", (username,))
    feedback_ids = [row[0] for row in conn.execute(
        "SELECT id FROM feedback WHERE username = ?", (username,)
    ).fetchall()]
    for feedback_id in feedback_ids:
        conn.execute("DELETE FROM feedback_replies WHERE feedback_id = ?", (feedback_id,))
    conn.execute("DELETE FROM feedback WHERE username = ?", (username,))
    conn.execute("DELETE FROM feedback_replies WHERE username = ?", (username,))
    clear_player_progress(conn, username)
    conn.execute("DELETE FROM users WHERE username = ?", (username,))

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
        clear_player_progress(conn, username)
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
        delete_player_account(conn, username)
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
        clear_player_progress(conn, username)
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
                conn.execute(
                    "INSERT OR IGNORE INTO debug_solved_state (username, challenge_id) VALUES (?, ?)",
                    (username, challenge["id"]),
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
        latest_feedback_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM feedback").fetchone()[0]
        latest_reply_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM feedback_replies").fetchone()[0]
        conn.execute("""
            INSERT INTO admin_notification_state(username, last_feedback_id, last_reply_id, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(username) DO UPDATE SET
                last_feedback_id = excluded.last_feedback_id,
                last_reply_id = excluded.last_reply_id,
                updated_at = CURRENT_TIMESTAMP
        """, (session["username"], latest_feedback_id, latest_reply_id))
        conn.commit()
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
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return render_template("debug_lab.html", lang=lang, username=session["username"], csrf_token=csrf_token)

# ==========================================
# API ENDPOINTS
# ==========================================

@app.route("/api/auth", methods=["POST"])
def auth():
    data = json_data()
    identifier = data.get("identifier", data.get("username", ""))
    pin = data.get("pin", "")
    avatar = data.get("avatar", "🐍")
    if avatar not in USER_AVATARS:
        avatar = "🐍"
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
                "INSERT INTO users (username, player_id, pin_hash, avatar) VALUES (?, ?, ?, ?)",
                (identifier, generate_player_id(cursor), hash_pin(pin), avatar),
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

    username = session["username"]
    bank = QUESTION_BANK[level]
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rotation = conn.execute("SELECT deck_json, cursor, last_batch_json FROM quiz_question_rotation WHERE username=? AND level=?",
                                (username, level)).fetchone()
        deck = json.loads(rotation["deck_json"]) if rotation else []
        cursor = int(rotation["cursor"]) if rotation else 0
        last_batch = json.loads(rotation["last_batch_json"]) if rotation else []
        if len(deck) != len(bank) or cursor + 10 > len(bank):
            deck = [q["id"] for q in bank]
            random.shuffle(deck)
            for _ in range(8):
                if deck[:10] != last_batch:
                    break
                random.shuffle(deck)
            cursor = 0
        question_by_id = {q["id"]: q for q in bank}
        selected_ids = deck[cursor:cursor + 10]
        questions = [question_by_id[qid] for qid in selected_ids]
        conn.execute("""INSERT INTO quiz_question_rotation(username,level,deck_json,cursor,last_batch_json)
                        VALUES(?,?,?,?,?) ON CONFLICT(username,level) DO UPDATE SET
                        deck_json=excluded.deck_json,cursor=excluded.cursor,last_batch_json=excluded.last_batch_json""",
                     (username, level, json.dumps(deck), cursor + 10, json.dumps(selected_ids)))
    shuffled_questions = []
    for original in questions:
        question = dict(original)
        indexed_options = list(enumerate(original["opts"]))
        random.shuffle(indexed_options)
        question["opts"] = [option for _, option in indexed_options]
        question["correct"] = next(index for index, (original_index, _) in enumerate(indexed_options)
                                   if original_index == original["correct"])
        shuffled_questions.append(question)
    # Keep answer keys and hints on the server. The browser receives only display data.
    clear_active_quiz()
    quiz = {
        "level": level,
        "questions": [{"id": q["id"], "correct": q["correct"], "hint": q["hint"], "exp": q["exp"]} for q in shuffled_questions],
        "index": 0,
        "score": 0,
        "lives": 3,
        "answered": False,
        "deadline": time.time() + 45,
        "hint_used": False,
    }
    save_active_quiz(quiz)
    public_questions = [{k: v for k, v in q.items() if k not in ("correct", "hint", "exp")} for q in shuffled_questions]
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
    if not isinstance(lang, str) or lang not in DEBUG_CHALLENGES:
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
        xp_row = conn.execute("SELECT COALESCE(SUM(xp), 0) AS total FROM debug_progress WHERE username = ?", (session["username"],)).fetchone()
        solved_rows = conn.execute("SELECT challenge_id FROM debug_solved_state WHERE username = ?", (session["username"],)).fetchall()
    return jsonify({"success": True, "xp": xp_row["total"] if xp_row else 0, "solved": [row["challenge_id"] for row in solved_rows]})

@app.route("/api/debug/reset", methods=["POST"])
def reset_debug_progress():
    username = session.get("username")
    if not username:
        return jsonify({"success": False, "message": "Sign in to reset Debug Lab practice."}), 401
    expected_csrf = session.get("csrf_token", "")
    supplied_csrf = request.headers.get("X-CSRF-Token", "")
    if not expected_csrf or not secrets.compare_digest(expected_csrf, supplied_csrf):
        return jsonify({"success": False, "message": "Session check failed. Refresh the page and try again."}), 403
    data = json_data()
    lang = data.get("language")
    if not isinstance(lang, str) or lang not in DEBUG_CHALLENGES:
        return jsonify({"success": False, "message": "Choose Python or C before resetting this lab."}), 400
    challenge_ids = [challenge["id"] for challenge in DEBUG_CHALLENGES[lang]]
    with get_db() as conn:
        placeholders = ",".join("?" for _ in challenge_ids)
        conn.execute(
            f"DELETE FROM debug_solved_state WHERE username = ? AND challenge_id IN ({placeholders})",
            (username, *challenge_ids),
        )
        conn.commit()
    return jsonify({"success": True, "message": "This lab's answered questions were reset. Your XP was kept."})

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
            conn.execute(
                "INSERT OR IGNORE INTO debug_solved_state (username, challenge_id) VALUES (?, ?)",
                (session["username"], challenge_id),
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
    auth_error = require_admin_api()
    if auth_error:
        return auth_error
    with get_db() as conn:
        target = conn.execute("SELECT role FROM users WHERE username = ?", (target_user,)).fetchone()
        if not target:
            return jsonify({"success": False, "message": "Account not found."}), 404
        if target["role"] == "admin":
            return jsonify({"success": False, "message": "Administrator accounts are protected."}), 403
        delete_player_account(conn, target_user)
        conn.commit()
    return jsonify({"success": True, "message": f"User '{target_user}' and their saved data were deleted."})

def valid_csrf():
    expected = session.get("csrf_token", "")
    return bool(expected and secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), expected))

@app.route("/arena")
def arena_page():
    if "username" not in session:
        return redirect(url_for("index"))
    token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    with get_db() as conn:
        account = conn.execute(
            "SELECT player_id FROM users WHERE username = ?", (session["username"],)
        ).fetchone()
    return render_template(
        "arena.html",
        csrf_token=token,
        username=session["username"],
        player_id=account["player_id"] if account else "—",
    )

@app.route("/api/arena/matchmake", methods=["POST"])
def arena_matchmake():
    username = session.get("username")
    if not username:
        return jsonify({"success": False, "message": "Sign in to enter the arena."}), 401
    if not valid_csrf():
        return jsonify({"success": False, "message": "Refresh the arena and try again."}), 403
    with get_db() as conn:
        conn.execute("DELETE FROM arena_queue WHERE queued_at < datetime('now','-3 minutes') AND match_id IS NULL")
        own = conn.execute("SELECT q.match_id,m.state FROM arena_queue q LEFT JOIN arena_matches m ON m.match_id=q.match_id WHERE q.username=?", (username,)).fetchone()
        if own and own["match_id"] and own["state"] != "complete":
            return jsonify({"success": True, "state": "matched", "match_id": own["match_id"]})
        if own and own["state"] == "complete":
            conn.execute("DELETE FROM arena_queue WHERE username=?", (username,))
        waiting = conn.execute("SELECT username,challenge_id FROM arena_queue WHERE username<>? AND match_id IS NULL ORDER BY queued_at LIMIT 1", (username,)).fetchone()
        if waiting:
            match_id = secrets.token_urlsafe(12)
            challenge_id = waiting["challenge_id"]
            conn.execute("INSERT INTO arena_matches(match_id,player_one,player_two,challenge_id) VALUES(?,?,?,?)", (match_id, waiting["username"], username, challenge_id))
            conn.execute("UPDATE arena_queue SET match_id=? WHERE username IN (?,?)", (match_id, waiting["username"], username))
            conn.execute("INSERT INTO arena_queue(username,match_id,challenge_id) VALUES(?,?,?) ON CONFLICT(username) DO UPDATE SET match_id=excluded.match_id,challenge_id=excluded.challenge_id,queued_at=CURRENT_TIMESTAMP", (username,match_id,challenge_id))
            conn.commit()
            return jsonify({"success": True, "state": "matched", "match_id": match_id})
        challenge_id = random.choice(DEBUG_CHALLENGES["python"])["id"]
        conn.execute("INSERT INTO arena_queue(username,challenge_id) VALUES(?,?) ON CONFLICT(username) DO UPDATE SET challenge_id=excluded.challenge_id,queued_at=CURRENT_TIMESTAMP,match_id=NULL", (username, challenge_id))
        conn.commit()
    return jsonify({"success": True, "state": "searching"})

@app.route("/api/arena/ai/start", methods=["POST"])
def arena_ai_start():
    username = session.get("username")
    if not username:
        return jsonify({"success": False, "message": "Sign in to enter the arena."}), 401
    if not valid_csrf():
        return jsonify({"success": False, "message": "Refresh the arena and try again."}), 403
    with get_db() as conn:
        current = conn.execute(
            "SELECT q.match_id, m.state, m.player_two FROM arena_queue q LEFT JOIN arena_matches m ON m.match_id=q.match_id WHERE q.username=?",
            (username,),
        ).fetchone()
        if current and current["match_id"] and current["state"] in ("active", "complete"):
            if current["state"] == "active":
                if current["player_two"] == "__CIPHER_BOT__":
                    return jsonify({
                        "success": True,
                        "state": "active",
                        "match_id": current["match_id"],
                        "opponent": "Cipher Bot",
                    })
                return jsonify({"success": False, "message": "Leave your live-player duel before starting a bot match."}), 409
            conn.execute("DELETE FROM arena_queue WHERE username=?", (username,))
        elif current:
            conn.execute("DELETE FROM arena_queue WHERE username=?", (username,))
        challenge_id = random.choice(DEBUG_CHALLENGES["python"])["id"]
        match_id = secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO arena_matches(match_id,player_one,player_two,challenge_id) VALUES(?,?,?,?)",
            (match_id, username, "__CIPHER_BOT__", challenge_id),
        )
        conn.execute(
            "INSERT INTO arena_queue(username,match_id,challenge_id) VALUES(?,?,?)",
            (username, match_id, challenge_id),
        )
        conn.commit()
    session.pop("arena_verified_match", None)
    return jsonify({"success": True, "state": "active", "match_id": match_id, "opponent": "Cipher Bot"})

@app.route("/api/arena/status")
def arena_status():
    username = session.get("username")
    if not username: return jsonify({"success": False, "message": "Sign in required."}), 401
    with get_db() as conn:
        row = conn.execute("SELECT q.match_id,q.queued_at,m.player_one,m.player_two,m.challenge_id,m.state,m.progress_one,m.progress_two,m.glitch_until,m.hack_by,m.created_at FROM arena_queue q LEFT JOIN arena_matches m ON m.match_id=q.match_id WHERE q.username=?", (username,)).fetchone()
    if not row: return jsonify({"success": True,"state":"idle"})
    if not row["match_id"]: return jsonify({"success": True,"state":"searching"})
    if row["player_two"] == "__CIPHER_BOT__":
        try:
            started_at = datetime.datetime.fromisoformat(str(row["created_at"]))
            # SQLite CURRENT_TIMESTAMP is UTC; compare in UTC to avoid local-zone
            # offsets making the bot appear to finish immediately.
            utc_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            elapsed = max(0, (utc_now - started_at).total_seconds())
        except (TypeError, ValueError):
            elapsed = 0
        bot_duration = 45 + int(hashlib.sha256(row["challenge_id"].encode()).hexdigest()[:2], 16) % 26
        progress_value = min(1.0, elapsed / bot_duration)
        bot_done = progress_value >= 1.0
        bot_progress = {"lines": int(progress_value * 7), "tests": int(progress_value >= .65), "submitted": bot_done}
        if bot_done and row["state"] == "active":
            with get_db() as conn:
                conn.execute("UPDATE arena_matches SET progress_two=?,state='complete' WHERE match_id=? AND state='active'",
                             (json.dumps(bot_progress), row["match_id"]))
                conn.commit()
            row = dict(row)
            row["state"] = "complete"
            row["progress_two"] = json.dumps(bot_progress)
    own_key = "progress_one" if username == row["player_one"] else "progress_two"
    other_key = "progress_two" if own_key == "progress_one" else "progress_one"
    own, opponent = json.loads(row[own_key] or "{}"), json.loads(row[other_key] or "{}")
    if row["player_two"] == "__CIPHER_BOT__":
        opponent_name = "Cipher Bot"
    else:
        other_player = row["player_two"] if username == row["player_one"] else row["player_one"]
        opponent_name = "Agent " + hashlib.sha256(other_player.encode()).hexdigest()[:4].upper()
    if row["state"] == "complete":
        winner = username if own.get("submitted") else opponent_name if opponent.get("submitted") else "Duel ended"
    else:
        winner = None
    response = {"success": True,"state":row["state"],"match_id":row["match_id"],"challenge_id":row["challenge_id"],"opponent":opponent_name,"own":own,"opponent_progress":{"lines":opponent.get("lines",0),"tests":opponent.get("tests",0),"submitted":opponent.get("submitted",False)},"glitched":row["hack_by"] != username and row["glitch_until"] > time.time(),"winner":winner}
    if row["state"] == "complete" and not own.get("submitted") and opponent.get("submitted"):
        challenge = next((item for item in DEBUG_CHALLENGES["python"] if item["id"] == row["challenge_id"]), None)
        solution = DEBUG_SOLUTIONS.get(row["challenge_id"])
        if challenge and solution:
            response["solution_review"] = {"code":solution, "explanation":challenge.get("hints", ["Review the condition and expected result."])[-1]}
    return jsonify(response)

@app.route("/api/arena/run",methods=["POST"])
def arena_run():
    username=session.get("username"); data=json_data(); code=data.get("code","")
    if not username: return jsonify({"success":False,"message":"Sign in required."}),401
    if not valid_csrf(): return jsonify({"success":False,"message":"Session check failed."}),403
    if not isinstance(code,str) or len(code)>12000: return jsonify({"success":False,"message":"Code must be 12,000 characters or fewer."}),400
    with get_db() as conn: match=conn.execute("SELECT m.match_id,m.challenge_id FROM arena_matches m JOIN arena_queue q ON q.match_id=m.match_id WHERE q.username=? AND m.state='active'",(username,)).fetchone()
    challenge=next((c for c in DEBUG_CHALLENGES['python'] if match and c['id']==match['challenge_id']),None)
    if not challenge:return jsonify({"success":False,"message":"No active duel challenge."}),404
    if rate_limited(CODE_ATTEMPTS,f"arena:{username}",20,60):return jsonify({"success":False,"message":"Runner limit reached. Wait and retry."}),429
    result=run_python_code(code)
    passed=result['success'] and result['stdout'].strip()==challenge['expectedOutput'].strip()
    if passed: session['arena_verified_match']=match['match_id']
    else: session.pop('arena_verified_match',None)
    return jsonify({"success":True,"passed":passed,"stdout":result['stdout'],"stderr":result['stderr'],"message":"All duel checks passed." if passed else "Output does not match the duel target."})

@app.route("/api/arena/progress", methods=["POST"])
def arena_progress():
    username=session.get("username"); data=json_data()
    if not username: return jsonify({"success":False,"message":"Sign in required."}),401
    if not valid_csrf(): return jsonify({"success":False,"message":"Session check failed."}),403
    lines=max(0,min(500,int(data.get("lines",0)))) if str(data.get("lines",0)).isdigit() else 0
    tests=0
    submitted=False
    with get_db() as conn:
        row=conn.execute("SELECT m.* FROM arena_matches m JOIN arena_queue q ON q.match_id=m.match_id WHERE q.username=? AND m.state='active'",(username,)).fetchone()
        if not row: return jsonify({"success":False,"message":"No active match."}),404
        submitted=bool(data.get("submitted",False) and session.get('arena_verified_match')==row['match_id'])
        tests=1 if session.get('arena_verified_match')==row['match_id'] else 0
        own_key="progress_one" if username==row["player_one"] else "progress_two"
        conn.execute(f"UPDATE arena_matches SET {own_key}=?,state=? WHERE match_id=?",(json.dumps({"lines":lines,"tests":tests,"submitted":submitted}),"complete" if submitted else "active",row["match_id"]))
        conn.commit()
    return jsonify({"success":True,"message":"Progress synced."})

@app.route("/api/arena/hack", methods=["POST"])
def arena_hack():
    username=session.get("username")
    if not username: return jsonify({"success":False,"message":"Sign in required."}),401
    if not valid_csrf(): return jsonify({"success":False,"message":"Session check failed."}),403
    with get_db() as conn:
        row=conn.execute("SELECT m.match_id,m.hack_by,m.player_two FROM arena_matches m JOIN arena_queue q ON q.match_id=m.match_id WHERE q.username=? AND m.state='active'",(username,)).fetchone()
        if not row: return jsonify({"success":False,"message":"No active duel."}),404
        if row["hack_by"]: return jsonify({"success":False,"message":"Distractor Hack has already been used."}),409
        if row["player_two"] == "__CIPHER_BOT__":
            conn.execute("UPDATE arena_matches SET hack_by=?,glitch_until=?,created_at=datetime(created_at,'+4 seconds') WHERE match_id=?",(username,time.time()+4,row["match_id"]))
            message = "Cipher Bot disrupted. Four seconds have been added to its timer."
        else:
            conn.execute("UPDATE arena_matches SET hack_by=?,glitch_until=? WHERE match_id=?",(username,time.time()+4,row["match_id"]))
            message = "Distractor deployed. Your live opponent's screen will glitch for four seconds."
        conn.commit()
    return jsonify({"success":True,"message":message})

@app.route("/api/arena/leave",methods=["POST"])
def arena_leave():
    if not session.get("username"): return jsonify({"success":False,"message":"Sign in required."}),401
    if not valid_csrf(): return jsonify({"success":False,"message":"Session check failed."}),403
    with get_db() as conn: conn.execute("DELETE FROM arena_queue WHERE username=?",(session["username"],));conn.commit()
    return jsonify({"success":True})

@app.route("/community")
def community_page():
    if "username" not in session: return redirect(url_for("index"))
    csrf=session.setdefault("csrf_token",secrets.token_urlsafe(32))
    with get_db() as conn:
        rows=conn.execute("SELECT id,username,title,language,difficulty,description,votes,created_at FROM community_puzzles WHERE status='approved' ORDER BY votes DESC,created_at DESC LIMIT 100").fetchall()
        player=conn.execute("SELECT role FROM users WHERE username=?",(session["username"],)).fetchone()
    return render_template("community.html", puzzles=rows, csrf_token=csrf, is_admin=bool(player and player["role"]=="admin"))

@app.route("/api/community/submit",methods=["POST"])
def community_submit():
    if "username" not in session: return jsonify({"success":False,"message":"Sign in to publish a puzzle."}),401
    if not valid_csrf(): return jsonify({"success":False,"message":"Refresh and try again."}),403
    data=json_data(); fields={key:data.get(key,"") for key in ("title","language","description","starter_code","expected_output","difficulty")}; solution=data.get("solution_code","")
    if fields["language"] not in ("python","c") or any(not isinstance(fields[k],str) for k in fields): return jsonify({"success":False,"message":"Check the puzzle fields."}),400
    for k,v in fields.items(): fields[k]=v.strip()
    if fields["difficulty"] not in ("Starter","Core","Challenge"):fields["difficulty"]="Core"
    if not isinstance(solution,str) or not solution.strip() or len(solution)>12000:return jsonify({"success":False,"message":"Add a reference solution so the test suite can verify your puzzle."}),400
    if not fields["title"] or len(fields["title"])>80 or not fields["description"] or len(fields["description"])>500 or not fields["starter_code"] or len(fields["starter_code"])>12000 or len(fields["expected_output"])>500: return jsonify({"success":False,"message":"Add a title, description, starter code, and concise expected output."}),400
    tested=run_python_code(solution) if fields["language"]=="python" else run_c_code(solution)
    if not tested["success"] or tested["stdout"].strip()!=fields["expected_output"].strip():return jsonify({"success":False,"message":"Reference solution failed the output check. Correct it before submitting."}),400
    with get_db() as conn: conn.execute("INSERT INTO community_puzzles(username,title,language,description,starter_code,expected_output,difficulty,status) VALUES(?,?,?,?,?,?,?,?)",(session["username"],fields["title"],fields["language"],fields["description"],fields["starter_code"],fields["expected_output"],fields["difficulty"],"approved" if is_current_admin() else "pending")); conn.commit()
    return jsonify({"success":True,"message":"Puzzle submitted for community review."})

@app.route("/api/community/<int:puzzle_id>/vote",methods=["POST"])
def community_vote(puzzle_id):
    if "username" not in session: return jsonify({"success":False,"message":"Sign in to vote."}),401
    if not valid_csrf(): return jsonify({"success":False,"message":"Refresh and try again."}),403
    with get_db() as conn:
        puzzle=conn.execute("SELECT 1 FROM community_puzzles WHERE id=? AND status='approved'",(puzzle_id,)).fetchone()
        if not puzzle:return jsonify({"success":False,"message":"Puzzle not found."}),404
        vote=conn.execute("INSERT OR IGNORE INTO community_votes(puzzle_id,username) VALUES(?,?)",(puzzle_id,session['username']))
        if vote.rowcount:conn.execute("UPDATE community_puzzles SET votes=votes+1 WHERE id=?",(puzzle_id,))
        conn.commit()
    return jsonify({"success":True,"message":"Vote counted." if vote.rowcount else "You already voted for this puzzle."})

@app.route("/api/announcement",methods=["GET","POST","DELETE"])
def announcement_api():
    if request.method=="GET":
        with get_db() as conn: row=conn.execute("SELECT message FROM site_announcements WHERE id=1").fetchone()
        return jsonify({"success":True,"message":row["message"] if row else ""})
    if not is_current_admin(): return jsonify({"success":False,"message":"Administrator access required."}),403
    if not valid_csrf(): return jsonify({"success":False,"message":"Session check failed."}),403
    if request.method=="DELETE":
        with get_db() as conn:
            conn.execute("DELETE FROM site_announcements WHERE id=1")
            conn.commit()
        return jsonify({"success":True,"message":"Announcement deleted."})
    message=json_data().get("message","")
    if not isinstance(message,str) or not message.strip() or len(message.strip())>220: return jsonify({"success":False,"message":"Enter an announcement of 1–220 characters."}),400
    with get_db() as conn:
        conn.execute("INSERT INTO site_announcements(id,message) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET message=excluded.message,updated_at=CURRENT_TIMESTAMP",(message.strip(),));conn.commit()
    return jsonify({"success":True,"message":"Announcement published."})

@app.route("/api/admin/community/<int:puzzle_id>/<action>",methods=["POST"])
def admin_moderate_puzzle(puzzle_id,action):
    if not is_current_admin(): return jsonify({"success":False,"message":"Administrator access required."}),403
    if not valid_csrf(): return jsonify({"success":False,"message":"Session check failed."}),403
    if action not in ("approve","reject"): return jsonify({"success":False,"message":"Unknown action."}),400
    with get_db() as conn:
        if action=="approve": cur=conn.execute("UPDATE community_puzzles SET status='approved' WHERE id=?",(puzzle_id,))
        else: cur=conn.execute("DELETE FROM community_puzzles WHERE id=?",(puzzle_id,))
        conn.commit()
    if not cur.rowcount:return jsonify({"success":False,"message":"Puzzle not found."}),404
    return jsonify({"success":True,"message":"Puzzle approved." if action=="approve" else "Puzzle removed."})

@app.route("/api/admin/ban/<player_id>",methods=["POST"])
def admin_toggle_ban(player_id):
    auth_error=require_admin_api()
    if auth_error:return auth_error
    banned=bool(json_data().get("banned"))
    with get_db() as conn:
        row=conn.execute("SELECT username,role FROM users WHERE player_id=?",(player_id,)).fetchone()
        if not row:return jsonify({"success":False,"message":"Account not found."}),404
        if row["role"]=="admin":return jsonify({"success":False,"message":"Admin accounts cannot be suspended here."}),403
        conn.execute("UPDATE users SET is_banned=? WHERE player_id=?",(1 if banned else 0,player_id));conn.commit()
    return jsonify({"success":True,"message":"Account suspended." if banned else "Account restored."})

@app.route("/logout")
def logout():
    clear_active_quiz()
    session.clear()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="127.0.0.1", port=5000)
