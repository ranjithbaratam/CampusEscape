// ==========================================
// CAMPUS ESCAPE - PYTHON CHALLENGE
// ==========================================

let score = 0;

let correctAnswers = 0;

let lives = 3;

let currentQuestion = 0;

let timeLeft = 30;

let timer;

let answered = false;


// ==========================================
// PLAYER
// ==========================================

const playerName =
    localStorage.getItem("playerName") || "Player";

document.getElementById("playerDisplay").innerText =
    "👤 Player: " + playerName;


// ==========================================
// QUESTIONS
// ==========================================

const questions = [

    {
        question:
            "What is the output of this code?",

        code:
`x = 10
x += 5
print(x)`,

        options: [
            "5",
            "10",
            "15",
            "20"
        ],

        answer: 2,

        hint:
            "🤖 AI Hint: += adds a value to the existing variable."
    },


    {
        question:
            "Which keyword is used to define a function in Python?",

        code: "",

        options: [
            "function",
            "def",
            "fun",
            "define"
        ],

        answer: 1,

        hint:
            "🤖 AI Hint: Python uses a short 3-letter keyword."
    },


    {
        question:
            "What is the output?",

        code:
`x = 5

if x > 3:
    print("Yes")
else:
    print("No")`,

        options: [
            "Yes",
            "No",
            "Error",
            "Nothing"
        ],

        answer: 0,

        hint:
            "🤖 AI Hint: Is 5 greater than 3?"
    },


    {
        question:
            "Which data type stores multiple values in an ordered collection?",

        code: "",

        options: [
            "int",
            "float",
            "list",
            "bool"
        ],

        answer: 2,

        hint:
            "🤖 AI Hint: Python lists use square brackets [ ]."
    },


    {
        question:
            "What is the output?",

        code:
`for i in range(3):
    print(i)`,

        options: [
            "1 2 3",
            "0 1 2",
            "0 1 2 3",
            "3"
        ],

        answer: 1,

        hint:
            "🤖 AI Hint: range(3) starts from 0."
    }

];


// ==========================================
// LOAD QUESTION
// ==========================================

function loadQuestion() {

    answered = false;

    const q = questions[currentQuestion];

    document.getElementById("questionNumber").innerText =
        "Question " +
        (currentQuestion + 1) +
        " / " +
        questions.length;

    document.getElementById("question").innerText =
        q.question;

    document.getElementById("code").innerText =
        q.code;


    for (let i = 0; i < 4; i++) {

        const button =
            document.getElementById("option" + i);

        button.innerText =
            String.fromCharCode(65 + i) +
            ") " +
            q.options[i];

        button.disabled = false;

    }


    document.getElementById("hint").innerText = "";

    document.getElementById("message").innerText = "";

    updateLives();

    updateProgress();

    startTimer();

}


// ==========================================
// CHECK ANSWER
// ==========================================

function checkAnswer(selected) {

    if (answered) {
        return;
    }

    const q = questions[currentQuestion];

    if (selected === q.answer) {

        answered = true;

        score += 10;

        correctAnswers++;

        document.getElementById("score").innerText =
            score;

        document.getElementById("message").innerText =
            "✅ Correct! +10 points";

        disableOptions();

        clearInterval(timer);

        setTimeout(nextQuestion, 900);

    }

    else {

        lives--;

        updateLives();

        document.getElementById("message").innerText =
            "❌ Wrong answer!";


        if (lives <= 0) {

            gameOver();

        }

    }

}


// ==========================================
// DISABLE OPTIONS
// ==========================================

function disableOptions() {

    for (let i = 0; i < 4; i++) {

        document.getElementById(
            "option" + i
        ).disabled = true;

    }

}


// ==========================================
// NEXT QUESTION
// ==========================================

function nextQuestion() {

    currentQuestion++;

    if (
        currentQuestion >=
        questions.length
    ) {

        levelComplete();

    }

    else {

        loadQuestion();

    }

}


// ==========================================
// LIVES
// ==========================================

function updateLives() {

    let hearts = "";

    for (let i = 0; i < lives; i++) {

        hearts += "❤️ ";

    }

    document.getElementById("lives").innerText =
        hearts;

}


// ==========================================
// PROGRESS BAR
// ==========================================

function updateProgress() {

    const progress =
        ((currentQuestion + 1) /
        questions.length) * 100;

    document.getElementById(
        "progressBar"
    ).style.width = progress + "%";

}


// ==========================================
// AI HINT
// ==========================================

function showHint() {

    const q =
        questions[currentQuestion];

    document.getElementById("hint").innerText =
        q.hint;

}


// ==========================================
// TIMER
// ==========================================

function startTimer() {

    clearInterval(timer);

    timeLeft = 30;

    document.getElementById("timer").innerText =
        timeLeft;


    timer = setInterval(function() {

        timeLeft--;

        document.getElementById("timer").innerText =
            timeLeft;


        if (timeLeft <= 0) {

            clearInterval(timer);

            lives--;

            updateLives();

            document.getElementById("message").innerText =
                "⏰ Time's up!";


            if (lives <= 0) {

                gameOver();

            }

            else {

                setTimeout(
                    nextQuestion,
                    1000
                );

            }

        }

    }, 1000);

}


// ==========================================
// GAME OVER
// ==========================================

function gameOver() {

    clearInterval(timer);

    localStorage.setItem(
        "pythonScore",
        score
    );

    localStorage.setItem(
        "pythonCorrect",
        correctAnswers
    );

    alert(
        "💀 Game Over!\nYour Python Score: " +
        score
    );

    window.location.href = "/";

}


// ==========================================
// LEVEL COMPLETE
// ==========================================

function levelComplete() {

    clearInterval(timer);

    localStorage.setItem(
        "pythonScore",
        score
    );

    localStorage.setItem(
        "pythonCorrect",
        correctAnswers
    );


    document.getElementById("message").innerText =
        "🎉 Python Level Complete!";


    setTimeout(function() {

        window.location.href = "/c";

    }, 1200);

}


// ==========================================
// START
// ==========================================

loadQuestion();