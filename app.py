import os
import sys
import sqlite3
import hashlib
import json
import random
import ssl
import tempfile
import subprocess
import urllib.request
import urllib.error
from flask import Flask, render_template, request, jsonify, session, redirect, url_for

app = Flask(__name__)
app.secret_key = "campus_escape_cyber_key_prod"
DB_NAME = "campus_escape.db"

# ==========================================
# MAIN QUIZ QUESTION BANK (10 QUESTIONS EACH)
# ==========================================

QUESTION_BANK = {
    "python": [
        {"id": 1, "text": "What is the output of print(3*2 + 1)?", "code": "print(3*2 + 1)", "opts": ["A) 7", "B) 9", "C) 6", "D) 8"], "correct": 0, "hint": "Check operator precedence.", "exp": "Multiplication evaluates first: 3*2=6, then 6+1=7."},
        {"id": 2, "text": "What is the output of x // 2?", "code": "x = 7\nprint(x // 2)", "opts": ["A) 2", "B) 3", "C) 3.5", "D) 4"], "correct": 1, "hint": "Floor division truncates decimals.", "exp": "Floor division // produces 3."},
        {"id": 3, "text": "Which method adds an item to a list?", "code": "", "opts": ["A) add()", "B) append()", "C) insertEnd()", "D) push()"], "correct": 1, "hint": "Standard list addition method.", "exp": "append() adds items to the list end."},
        {"id": 4, "text": "What is the output of a ** b?", "code": "a = 4\nb = 2\nprint(a ** b)", "opts": ["A) 6", "B) 8", "C) 16", "D) 12"], "correct": 2, "hint": "** is the power operator.", "exp": "4 raised to power 2 is 16."},
        {"id": 5, "text": "What does input() return by default?", "code": "", "opts": ["A) int", "B) float", "C) string", "D) boolean"], "correct": 2, "hint": "Raw console input type.", "exp": "input() returns data as a string."},
        {"id": 6, "text": "What is the output of len('HELLO')?", "code": "print(len('HELLO'))", "opts": ["A) 4", "B) 5", "C) 6", "D) 0"], "correct": 1, "hint": "Count character length.", "exp": "'HELLO' contains 5 letters."},
        {"id": 7, "text": "Which keyword exits a loop immediately?", "code": "", "opts": ["A) stop", "B) break", "C) exit", "D) skip"], "correct": 1, "hint": "Loop termination statement.", "exp": "break terminates the active loop."},
        {"id": 8, "text": "What is the output of negative indexing?", "code": "nums = [2, 4, 6]\nprint(nums[-1])", "opts": ["A) 2", "B) 4", "C) 6", "D) Error"], "correct": 2, "hint": "Index -1 targets the final element.", "exp": "-1 extracts the last item: 6."},
        {"id": 9, "text": "What is the result of x == 5?", "code": "x = 5\nprint(x == 5)", "opts": ["A) 5", "B) True", "C) False", "D) Error"], "correct": 1, "hint": "Boolean equality check.", "exp": "5 == 5 evaluates to True."},
        {"id": 10, "text": "Which operator performs exponentiation?", "code": "", "opts": ["A) ^", "B) **", "C) //", "D) %%"], "correct": 1, "hint": "Double asterisks symbol.", "exp": "** calculates powers in Python."}
    ],
    "c": [
        {"id": 1, "text": "What is the output of 4 + 3 * 2 in C?", "code": "printf(\"%d\", 4 + 3 * 2);", "opts": ["A) 14", "B) 10", "C) 11", "D) 8"], "correct": 1, "hint": "Operator precedence.", "exp": "Multiplication precedes addition: 3*2=6; 4+6=10."},
        {"id": 2, "text": "Which data type stores a single character?", "code": "", "opts": ["A) int", "B) char", "C) float", "D) double"], "correct": 1, "hint": "Single character byte type.", "exp": "char stores single character values in C."},
        {"id": 3, "text": "What is the output of integer division 8 / 3?", "code": "int x = 8;\nprintf(\"%d\", x / 3);", "opts": ["A) 2", "B) 2.66", "C) 3", "D) 1"], "correct": 0, "hint": "Truncates fractional part.", "exp": "8 / 3 truncates decimal remainder to 2."},
        {"id": 4, "text": "Which operator checks equality between values?", "code": "", "opts": ["A) =", "B) ==", "C) !=", "D) <="], "correct": 1, "hint": "Double equals sign.", "exp": "== performs equality tests."},
        {"id": 5, "text": "What is the output after post-incrementing a?", "code": "int a = 5;\na++;\nprintf(\"%d\", a);", "opts": ["A) 5", "B) 6", "C) 4", "D) 7"], "correct": 1, "hint": "Increments variable by 1.", "exp": "Post-increment increases value to 6."},
        {"id": 6, "text": "Which format specifier is used for floating-point numbers?", "code": "", "opts": ["A) %d", "B) %c", "C) %f", "D) %s"], "correct": 2, "hint": "% + f for float.", "exp": "%f prints floating point decimal values."},
        {"id": 7, "text": "What is the result of 9 % 4 in C?", "code": "printf(\"%d\", 9 % 4);", "opts": ["A) 1", "B) 2", "C) 3", "D) 4"], "correct": 0, "hint": "Modulo returns remainder.", "exp": "9 divided by 4 leaves remainder 1."},
        {"id": 8, "text": "Which loop condition is checked AFTER execution?", "code": "", "opts": ["A) while", "B) for", "C) do-while", "D) if"], "correct": 2, "hint": "Post-test execution loop.", "exp": "do-while runs at least once before testing condition."},
        {"id": 9, "text": "What does sizeof(int) return?", "code": "", "opts": ["A) Value of int", "B) Byte size in memory", "C) Address", "D) Variable name"], "correct": 1, "hint": "Measures memory space.", "exp": "sizeof returns storage size in bytes."},
        {"id": 10, "text": "Which function reads input from the user console?", "code": "", "opts": ["A) printf()", "B) scanf()", "C) input()", "D) read()"], "correct": 1, "hint": "Standard input scanner.", "exp": "scanf() parses console input streams."}
    ],
    "logic": [
        {"id": 1, "text": "A number is doubled and 3 added. Starting with 4, what is the result?", "code": "", "opts": ["A) 8", "B) 11", "C) 14", "D) 7"], "correct": 1, "hint": "Double 4, then add 3.", "exp": "4*2=8, 8+3=11."},
        {"id": 2, "text": "Find the next sequence value: 5, 9, 13, 17, ?", "code": "", "opts": ["A) 20", "B) 21", "C) 22", "D) 23"], "correct": 1, "hint": "Add +4 pattern.", "exp": "17 + 4 = 21."},
        {"id": 3, "text": "Riya is taller than Anu. Anu is taller than Meena. Who is shortest?", "code": "", "opts": ["A) Riya", "B) Anu", "C) Meena", "D) Undetermined"], "correct": 2, "hint": "Order heights sequentially.", "exp": "Order: Riya > Anu > Meena."},
        {"id": 4, "text": "If 3 pencils cost $15, what is the cost of 5 pencils?", "code": "", "opts": ["A) $20", "B) $25", "C) $30", "D) $35"], "correct": 1, "hint": "Calculate unit cost first.", "exp": "1 pencil = $5; 5 pencils = $25."},
        {"id": 5, "text": "Identify the odd one out: 9, 16, 25, 30", "code": "", "opts": ["A) 9", "B) 16", "C) 25", "D) 30"], "correct": 3, "hint": "Check perfect square values.", "exp": "30 is not a perfect square."},
        {"id": 6, "text": "A train leaves at 8:20 and travels for 40 minutes. Arrival time?", "code": "", "opts": ["A) 8:50", "B) 9:00", "C) 9:10", "D) 9:20"], "correct": 1, "hint": "Add 40 minutes.", "exp": "8:20 + 40 mins = 9:00."},
        {"id": 7, "text": "If A=1, B=2, C=3, what is the sum value of BAD?", "code": "", "opts": ["A) 6", "B) 7", "C) 8", "D) 9"], "correct": 1, "hint": "B(2) + A(1) + D(4).", "exp": "2 + 1 + 4 = 7."},
        {"id": 8, "text": "Find missing number: 2, 6, 12, 20, ?", "code": "", "opts": ["A) 28", "B) 30", "C) 32", "D) 34"], "correct": 1, "hint": "Differences grow +4, +6, +8...", "exp": "20 + 10 = 30."},
        {"id": 9, "text": "If today is Wednesday, what day is it 9 days later?", "code": "", "opts": ["A) Thursday", "B) Friday", "C) Saturday", "D) Sunday"], "correct": 1, "hint": "9 days = 1 week + 2 days.", "exp": "Wednesday + 2 days = Friday."},
        {"id": 10, "text": "5 people shake hands with each other once. Total handshakes?", "code": "", "opts": ["A) 5", "B) 8", "C) 10", "D) 15"], "correct": 2, "hint": "n*(n-1)/2 calculation.", "exp": "(5 * 4) / 2 = 10 handshakes."}
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
        }
    ],
    "c": [
        {
            "id": "c001",
            "title": "BUG #01 — Wrong Format Specifier",
            "difficulty": 1,
            "description": "Fix the format specifier in printf so that it correctly prints the integer x.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    int x = 25;\n    printf(\"%f\\n\", x);\n    return 0;\n}",
            "options": [
                "A) Change %f to %d",
                "B) Change %f to %s",
                "C) Remove %f from printf",
                "D) Change int x to float x"
            ],
            "correctOpt": 0,
            "explanation": "%d is the format specifier for integers in C.",
            "hints": ["Look at the variable type of x.", "x is an int.", "Int variables require %d."],
            "xp": 100
        },
        {
            "id": "c002",
            "title": "BUG #02 — Missing Semicolon",
            "difficulty": 1,
            "description": "Fix the syntax error so that the program compiles cleanly.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    printf(\"Campus Escape\\n\")\n    return 0;\n}",
            "options": [
                "A) Add ';' at the end of line 4",
                "B) Add ';' after int main()",
                "C) Remove \\n from printf",
                "D) Change return 0 to return 1;"
            ],
            "correctOpt": 0,
            "explanation": "Every statement in C must end with a semicolon ';'.",
            "hints": ["Check statement terminators.", "Line 4 is missing something at the end.", "Add a semicolon."],
            "xp": 100
        },
        {
            "id": "c003",
            "title": "BUG #03 — Assignment vs Equality",
            "difficulty": 2,
            "description": "Fix the if statement so it performs a comparison rather than assignment.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    int key = 5;\n    if (key = 5) {\n        printf(\"UNLOCKED\\n\");\n    }\n    return 0;\n}",
            "options": [
                "A) Change (key = 5) to (key == 5)",
                "B) Change (key = 5) to (key != 5)",
                "C) Change int key = 5 to float key = 5",
                "D) Remove the if statement"
            ],
            "correctOpt": 0,
            "explanation": "'==' compares equality, whereas '=' assigns a value.",
            "hints": ["Single '=' is assignment.", "Double '==' tests equality.", "Use == in condition."],
            "xp": 150
        },
        {
            "id": "c004",
            "title": "BUG #04 — Array Out of Bounds",
            "difficulty": 3,
            "description": "Fix the loop condition so it prints all 3 elements without array out-of-bounds.",
            "starterCode": "#include <stdio.h>\n\nint main() {\n    int arr[3] = {10, 20, 30};\n    for(int i = 0; i <= 3; i++) {\n        printf(\"%d\\n\", arr[i]);\n    }\n    return 0;\n}",
            "options": [
                "A) Change i <= 3 to i < 3",
                "B) Change int arr[3] to int arr[4]",
                "C) Change i++ to i--",
                "D) Start loop at i = 1"
            ],
            "correctOpt": 0,
            "explanation": "An array of size 3 has indices 0, 1, and 2. i <= 3 goes out of bounds.",
            "hints": ["Array size is 3.", "Valid indices are 0, 1, 2.", "Change condition to i < 3."],
            "xp": 200
        }
    ]
}
# Database Helpers
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                pin_hash TEXT NOT NULL,
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
        conn.commit()

init_db()

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

# ==========================================
# CODE EXECUTION RUNNERS
# ==========================================

def run_python_code(source_code: str, timeout_sec: int = 4):
    """Executes Python code cleanly, capturing exact output and trimming whitespace safely."""
    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = os.path.join(temp_dir, "solution.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(source_code)

        try:
            process = subprocess.run(
                [sys.executable, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_sec,
                cwd=temp_dir
            )
            return {
                "success": process.returncode == 0,
                "stdout": process.stdout.strip(),
                "stderr": process.stderr.strip(),
                "exit_code": process.returncode
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": f"TIMED OUT: Exceeded {timeout_sec}s limit.", "exit_code": -1}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": f"EXECUTION ERROR: {str(e)}", "exit_code": -1}

def run_c_code(source_code: str, timeout_sec: int = 5):
    """
    Executes C code cleanly using the Piston Execution Engine API.
    Does NOT require local GCC/MinGW or manual compiler configuration.
    """
    url = "https://emkc.org/api/v2/piston/execute"
    
    payload = json.dumps({
        "language": "c",
        "version": "*",
        "files": [
            {
                "name": "main.c",
                "content": source_code
            }
        ]
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CampusEscape/2.0"
    }

    # SSL context bypass for local development machines
    ssl_context = ssl._create_unverified_context()

    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_sec, context=ssl_context) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            
            compile_stage = res_data.get("compile", {})
            run_stage = res_data.get("run", {})
            
            compile_stderr = compile_stage.get("stderr", "").strip() if compile_stage else ""
            if compile_stderr or (compile_stage and compile_stage.get("code", 0) != 0):
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"COMPILATION ERROR:\n{compile_stderr}",
                    "exit_code": 1
                }

            stdout = run_stage.get("stdout", "").strip()
            stderr = run_stage.get("stderr", "").strip()
            exit_code = run_stage.get("code", 0)

            return {
                "success": exit_code == 0,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code
            }

    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"C Compiler service error: {str(e)}. Click RUN CODE to retry.",
            "exit_code": -1
        }

# ==========================================
# PAGE ROUTING
# ==========================================

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/map")
@app.route("/dashboard")
def map_page():
    if "username" not in session:
        return redirect(url_for("index"))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT python_score, c_score, logic_score 
            FROM results WHERE username = ? ORDER BY id DESC LIMIT 1
        """, (session["username"],))
        res = cursor.fetchone()

    py_s = res["python_score"] if res else 0
    c_s = res["c_score"] if res else 0
    log_s = res["logic_score"] if res else 0
    
    tot_score = py_s + c_s + log_s
    accuracy = (tot_score / 3000.0) * 100 if tot_score > 0 else 0

    unlock_c = py_s > 0
    unlock_logic = py_s > 0 and c_s > 0
    all_completed = (py_s > 0) and (c_s > 0) and (log_s > 0)
    unlock_vault = all_completed and (accuracy >= 60.0)

    return render_template(
        "map.html", 
        username=session["username"],
        py_s=py_s, c_s=c_s, log_s=log_s,
        unlock_c=unlock_c,
        unlock_logic=unlock_logic,
        unlock_vault=unlock_vault,
        all_completed=all_completed,
        accuracy=round(accuracy, 1)
    )

@app.route("/game")
def python_game():
    return render_template("game.html", level_id="python", level_name="Python Lab", level_num="1")

@app.route("/c")
def c_game():
    return render_template("game.html", level_id="c", level_name="C Programming Lab", level_num="2")

@app.route("/logic")
def logic_game():
    return render_template("game.html", level_id="logic", level_name="Logic Lab", level_num="3")

@app.route("/lock")
def password_room():
    return render_template("lock.html")

@app.route("/debug")
def debug_select():
    if "username" not in session:
        return redirect(url_for("index"))
    return render_template("debug_select.html", username=session["username"])

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
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    pin = data.get("pin", "").strip()

    if not username or not pin:
        return jsonify({"success": False, "message": "Username and 4-digit PIN required."}), 400

    pin_h = hash_pin(pin)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()

        if user:
            if user["pin_hash"] == pin_h:
                session["username"] = username
                return jsonify({"success": True, "message": "Login successful!"})
            else:
                return jsonify({"success": False, "message": "Invalid PIN for user."}), 401
        else:
            cursor.execute("INSERT INTO users (username, pin_hash) VALUES (?, ?)", (username, pin_h))
            conn.commit()
            session["username"] = username
            return jsonify({"success": True, "message": "Account created!"})

@app.route("/api/get-questions/<level>", methods=["GET"])
def get_questions(level):
    if level not in QUESTION_BANK:
        return jsonify({"success": False, "message": "Invalid level."}), 400

    sample_size = min(10, len(QUESTION_BANK[level]))
    questions = random.sample(QUESTION_BANK[level], sample_size)
    return jsonify({"success": True, "questions": questions})

@app.route("/api/save-score", methods=["POST"])
def save_score():
    if "username" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    data = request.get_json() or {}
    py_score = data.get("python_score", 0)
    c_score = data.get("c_score", 0)
    logic_score = data.get("logic_score", 0)

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
        accuracy = (tot / 3000.0) * 100

        cursor.execute("""
            INSERT INTO results (username, python_score, c_score, logic_score, total_score, accuracy)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session["username"], new_py, new_c, new_log, tot, accuracy))

        cursor.execute("""
            UPDATE users SET best_score = MAX(best_score, ?), attempts = attempts + 1 WHERE username = ?
        """, (tot, session["username"]))
        conn.commit()

    return jsonify({"success": True, "total_score": tot, "accuracy": round(accuracy, 1)})

@app.route("/api/debug/challenges/<lang>", methods=["GET"])
def get_debug_challenges(lang):
    if lang not in DEBUG_CHALLENGES:
        return jsonify({"success": False, "message": "Language not supported."}), 400
    return jsonify({"success": True, "challenges": DEBUG_CHALLENGES[lang]})

@app.route("/api/debug/run", methods=["POST"])
def run_code():
    data = request.get_json() or {}
    lang = data.get("language", "python").lower()
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
    data = request.get_json() or {}
    lang = data.get("language", "python").lower()
    challenge_id = data.get("challenge_id", "")
    code = data.get("code", "")

    challenges = DEBUG_CHALLENGES.get(lang, [])
    target = next((c for c in challenges if c["id"] == challenge_id), None)

    if not target:
        return jsonify({"success": False, "message": "Challenge ID not found."}), 404

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

    return jsonify({
        "passed": passed,
        "stdout": actual_output,
        "stderr": res["stderr"],
        "expected": expected_output,
        "xp": target["xp"] if passed else 0,
        "message": "✓ ALL TEST CASES PASSED!" if passed else "✗ Output does not match expected output."
    })

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)