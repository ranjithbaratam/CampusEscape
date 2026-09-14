let questions = [], qIdx = 0, score = 0, lives = 3, timer = 30, timerInterval;
let selectedOptIdx = null;
let isChecked = false;

async function loadQuestions() {
    let level = window.CURRENT_LEVEL || 'python';
    if (level === '1' || level.includes('py')) level = 'python';

    try {
        const res = await fetch(`/api/get-questions/${level}`);
        const data = await res.json();
        
        if (data.success && data.questions.length > 0) {
            questions = data.questions;
            renderQ();
        } else {
            document.getElementById('qText').innerText = "Failed to load sector questions. Please return to map.";
        }
    } catch (err) {
        document.getElementById('qText').innerText = "Network Error: Could not connect to game server.";
    }
}

function startTimer() {
    clearInterval(timerInterval);
    timer = 30;
    document.getElementById('timerVal').innerText = `${timer}s`;
    timerInterval = setInterval(() => {
        timer--;
        document.getElementById('timerVal').innerText = `${timer}s`;
        if (timer <= 0) { clearInterval(timerInterval); handleTimeout(); }
    }, 1000);
}

function renderQ() {
    isChecked = false;
    selectedOptIdx = null;
    const q = questions[qIdx];

    // Dynamic total length check
    const totalQ = questions.length;
    document.getElementById('qNum').innerText = `Question ${qIdx + 1} / ${totalQ}`;
    document.getElementById('progressFill').style.width = `${((qIdx + 1) / totalQ) * 100}%`;
    document.getElementById('qText').innerText = q.text;

    const codeEl = document.getElementById('qCode');
    if (q.code) { codeEl.style.display = 'block'; codeEl.innerText = q.code; }
    else { codeEl.style.display = 'none'; }

    document.getElementById('hintBox').style.display = 'none';
    document.getElementById('expBox').style.display = 'none';
    
    const actionBtn = document.getElementById('actionBtn');
    actionBtn.innerText = "CHECK ANSWER";
    actionBtn.disabled = true;

    const optsEl = document.getElementById('optsContainer');
    optsEl.innerHTML = '';
    q.opts.forEach((opt, idx) => {
        const btn = document.createElement('button');
        btn.className = 'opt-btn';
        btn.innerText = opt;
        btn.onclick = () => selectOpt(idx, btn);
        optsEl.appendChild(btn);
    });

    startTimer();
}
function selectOpt(idx, btn) {
    if (isChecked) return;
    if (typeof playSfx === 'function') playSfx('click');
    selectedOptIdx = idx;
    
    document.querySelectorAll('.opt-btn').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    document.getElementById('actionBtn').disabled = false;
}

function useHint() {
    if (typeof playSfx === 'function') playSfx('click');
    timer = Math.max(1, timer - 5);
    document.getElementById('timerVal').innerText = `${timer}s`;
    const h = document.getElementById('hintBox');
    h.innerText = `💡 AI Hint: ${questions[qIdx].hint}`;
    h.style.display = 'block';
}

function handleAction() {
    if (!isChecked) {
        isChecked = true;
        clearInterval(timerInterval);
        const q = questions[qIdx];
        const allBtns = document.querySelectorAll('.opt-btn');

        if (selectedOptIdx === q.correct) {
            if (typeof playSfx === 'function') playSfx('correct');
            allBtns[selectedOptIdx].classList.add('correct');
            score += 100;
            document.getElementById('scoreVal').innerText = score;
        } else {
            if (typeof playSfx === 'function') playSfx('wrong');
            if (selectedOptIdx !== null) allBtns[selectedOptIdx].classList.add('wrong');
            allBtns[q.correct].classList.add('correct');
            lives--;
            updateLives();
            document.getElementById('expText').innerText = q.exp;
            document.getElementById('expBox').style.display = 'block';
        }

        document.getElementById('actionBtn').innerText = "NEXT QUESTION →";
    } else {
        if (typeof playSfx === 'function') playSfx('click');
        qIdx++;
        if (qIdx < questions.length && lives > 0) renderQ();
        else saveAndExit();
    }
}

function handleTimeout() {
    isChecked = true;
    if (typeof playSfx === 'function') playSfx('wrong');
    const q = questions[qIdx];
    document.querySelectorAll('.opt-btn')[q.correct].classList.add('correct');
    lives--;
    updateLives();
    document.getElementById('expText').innerText = "Time expired! " + q.exp;
    document.getElementById('expBox').style.display = 'block';
    
    const actionBtn = document.getElementById('actionBtn');
    actionBtn.innerText = "NEXT QUESTION →";
    actionBtn.disabled = false;
}

function updateLives() {
    let h = ''; for (let i = 0; i < 3; i++) h += i < lives ? '❤️' : '🖤';
    document.getElementById('livesVal').innerText = h;
    if (lives <= 0) setTimeout(() => { alert('Game Over!'); saveAndExit(); }, 300);
}

async function saveAndExit() {
    clearInterval(timerInterval);
    let payload = {};
    let level = window.CURRENT_LEVEL || 'python';
    if (level === 'python') payload.python_score = score;
    if (level === 'c') payload.c_score = score;
    if (level === 'logic') payload.logic_score = score;

    try {
        await fetch('/api/save-score', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
    } catch (e) {}
    window.location.href = '/map';
}

document.addEventListener("DOMContentLoaded", loadQuestions);