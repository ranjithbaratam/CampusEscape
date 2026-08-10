from flask import Flask, render_template

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/game")
def game():
    return render_template("game.html")


@app.route("/c")
def c_level():
    return render_template("c.html")


@app.route("/logic")
def logic_level():
    return render_template("logic.html")


@app.route("/lock")
def lock():
    return render_template("lock.html")


@app.route("/ai")
def ai_challenge():
    return render_template("ai.html")


@app.route("/result")
def result():
    return render_template("result.html")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )