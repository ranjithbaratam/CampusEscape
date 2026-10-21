let questions = [], qIdx = 0, score = 0, lives = 3, timer = 45, timerInterval;
let selectedOptIdx = null;
let isChecked = false;
let hintUsed = false;
let answeredCount = 0;

async function apiPost(url, data = {}) {
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.message || 'Request failed.');
    return result;
}

async function loadQuestions() {
    let level = window.CURRENT_LEVEL || 'python';
    if (level === '1' || level.includes('py')) level = 'python';
    window.CURRENT_LEVEL = level;
    try {
        const res = await fetch(`/api/get-questions/${level}`);
        const data = await res.json();
        if (data.success && data.questions.length) {
            questions = data.questions;
            document.querySelector('.progress-bar-bg')?.setAttribute('aria-valuemax', questions.length);
            document.querySelector('.mission-rules')?.setAttribute('aria-label', `${questions.length} questions, 100 points per correct answer`);
            renderQ();
        } else {
            showGameError(new Error(data.message || 'Failed to load sector questions. Please return to map.'));
        }
    } catch (err) {
        showGameError(new Error('Network error: could not connect to the game server.'));
    }
}

function startTimer() {
    clearInterval(timerInterval);
    timer = 45;
    document.getElementById('timerVal').innerText = `${timer}s`;
    timerInterval = setInterval(() => {
        timer--;
        document.getElementById('timerVal').innerText = `${timer}s`;
        const fill = document.getElementById('timerFill');
        fill.style.width = `${Math.max(0, timer / 45 * 100)}%`;
        fill.classList.toggle('urgent', timer <= 10);
        document.querySelector('.timer-track').setAttribute('aria-valuenow', Math.max(0, timer));
        if (timer <= 0) { clearInterval(timerInterval); handleTimeout(); }
    }, 1000);
}

function renderQ() {
    isChecked = false;
    hintUsed = false;
    selectedOptIdx = null;
    const q = questions[qIdx];
    const totalQ = questions.length;
    document.getElementById('qNum').innerText = `Question ${qIdx + 1} / ${totalQ}`;
    document.getElementById('qMeta').innerText = [q.topic, q.difficulty].filter(Boolean).join(' · ');
    document.getElementById('progressFill').style.width = `${((qIdx + 1) / totalQ) * 100}%`;
    document.querySelector('.progress-bar-bg').setAttribute('aria-valuenow', qIdx + 1);
    document.getElementById('progressCaption').innerText = `Question ${qIdx + 1} of ${totalQ}`;
    document.getElementById('qText').innerText = q.text;
    const codeEl = document.getElementById('qCode');
    if (q.code) { codeEl.style.display = 'block'; codeEl.innerText = q.code; }
    else { codeEl.style.display = 'none'; }
    document.getElementById('hintBox').style.display = 'none';
    document.getElementById('expBox').style.display = 'none';
    const status = document.getElementById('gameStatus');
    if (status) { status.hidden = true; status.innerText = ''; }
    const actionBtn = document.getElementById('actionBtn');
    actionBtn.innerText = 'CHECK ANSWER';
    actionBtn.disabled = true;
    document.getElementById('hintBtn').disabled = false;
    document.getElementById('timerFill').style.width = '100%';
    document.getElementById('timerFill').classList.remove('urgent');
    document.querySelector('.timer-track').setAttribute('aria-valuenow', 45);
    const optsEl = document.getElementById('optsContainer');
    optsEl.innerHTML = '';
    q.opts.forEach((opt, idx) => {
        const btn = document.createElement('button');
        btn.className = 'opt-btn';
        btn.type = 'button';
        btn.setAttribute('aria-pressed', 'false');
        const letter = document.createElement('span');
        letter.className = 'option-letter';
        letter.textContent = String.fromCharCode(65 + idx);
        const label = document.createElement('span');
        label.className = 'option-label';
        label.textContent = opt;
        btn.append(letter, label);
        btn.onclick = () => selectOpt(idx, btn);
        optsEl.appendChild(btn);
    });
    startTimer();
}

function selectOpt(idx, btn) {
    if (isChecked) return;
    if (typeof playSfx === 'function') playSfx('click');
    selectedOptIdx = idx;
    document.querySelectorAll('.opt-btn').forEach(b => {
        b.classList.remove('selected');
        b.setAttribute('aria-pressed', 'false');
    });
    btn.classList.add('selected');
    btn.setAttribute('aria-pressed', 'true');
    document.getElementById('actionBtn').disabled = false;
}

async function useHint() {
    if (isChecked || hintUsed) return;
    try {
        const data = await apiPost('/api/quiz/hint');
        hintUsed = true;
        document.getElementById('hintBtn').disabled = true;
        if (typeof playSfx === 'function') playSfx('click');
        const hint = document.getElementById('hintBox');
        hint.innerText = `💡 Hint: ${data.hint}`;
        hint.style.display = 'block';
    } catch (err) { showGameError(err); }
}

async function submitAnswer(answer) {
    if (isChecked) return;
    isChecked = true;
    clearInterval(timerInterval);
    answeredCount++;
    const actionBtn = document.getElementById('actionBtn');
    actionBtn.disabled = true;
    try {
        const result = await apiPost('/api/quiz/answer', { answer });
        score = result.score;
        lives = result.lives;
        document.getElementById('scoreVal').innerText = score;
        updateLives();
        const buttons = document.querySelectorAll('.opt-btn');
        buttons.forEach(button => { button.disabled = true; });
        if (answer !== null && !result.correct) buttons[answer]?.classList.add('wrong');
        buttons[result.correct_index]?.classList.add('correct');
        document.getElementById('expText').innerText = result.timed_out ? `Time expired. ${result.explanation}` : result.explanation;
        document.getElementById('expHeading').innerText = result.correct ? 'Why this is correct: ' : 'Review this answer: ';
        document.getElementById('expBox').classList.toggle('is-correct', result.correct);
        document.getElementById('expBox').style.display = 'block';
        if (!result.correct) {
            if (typeof playSfx === 'function') playSfx('wrong');
        } else if (typeof playSfx === 'function') playSfx('correct');
        actionBtn.innerText = 'NEXT QUESTION →';
        actionBtn.disabled = false;
    } catch (err) {
        answeredCount = Math.max(0, answeredCount - 1);
        isChecked = false;
        actionBtn.disabled = selectedOptIdx === null;
        showGameError(err);
    }
}

async function handleAction() {
    if (!isChecked) return submitAnswer(selectedOptIdx);
    if (typeof playSfx === 'function') playSfx('click');
    clearInterval(timerInterval);
    try {
        const result = await apiPost('/api/quiz/next');
        if (result.finished) {
            if (lives <= 0) alert('Game Over!');
            await saveAndExit();
        } else {
            qIdx++;
            renderQ();
        }
    } catch (err) { showGameError(err); }
}

function handleTimeout() { submitAnswer(null); }

function updateLives() {
    let hearts = '';
    for (let i = 0; i < 3; i++) hearts += i < lives ? '❤️' : '🖤';
    const lifeDisplay = document.getElementById('livesVal');
    lifeDisplay.innerText = hearts;
    lifeDisplay.setAttribute('aria-label', `${lives} ${lives === 1 ? 'life' : 'lives'} remaining`);
}

async function saveAndExit() {
    clearInterval(timerInterval);
    let saved;
    try {
        saved = await apiPost('/api/save-score', { level: window.CURRENT_LEVEL });
    } catch (err) { showGameError(err); return; }
    document.getElementById('finishTitle').innerText = saved.sector_cleared ? 'Sector cleared!' : 'Run complete';
    document.getElementById('finishScore').innerText = score;
    document.getElementById('finishCorrect').innerText = `${score / 100}/${answeredCount}`;
    document.getElementById('finishAccuracy').innerText = `${answeredCount ? Math.round((score / 100) / answeredCount * 100) : 0}%`;
    document.getElementById('finishMessage').innerText = saved.sector_cleared
        ? 'You completed every question without using all three lives. Your score is saved to your dashboard.'
        : 'This run is saved, but the sector remains locked until you finish all questions. Review the explanations and try again.';
    document.getElementById('finishOverlay').style.display = 'flex';
}

function showGameError(error) {
    const status = document.getElementById('gameStatus');
    if (!status) return;
    status.innerText = error.message || 'Unable to contact the game server. Please try again.';
    status.hidden = false;
}

function retrySector() { window.location.reload(); }

document.addEventListener('keydown', event => {
    if (isChecked) {
        if (event.key === 'Enter' && !document.getElementById('actionBtn').disabled) {
            event.preventDefault();
            handleAction();
        }
        return;
    }
    if (event.key === 'Enter' && selectedOptIdx !== null) {
        event.preventDefault();
        submitAnswer(selectedOptIdx);
        return;
    }
    if (/^[1-4]$/.test(event.key)) {
        const index = Number(event.key) - 1;
        const option = document.querySelectorAll('.opt-btn')[index];
        if (option) { event.preventDefault(); selectOpt(index, option); }
    }
}, true);

document.addEventListener('DOMContentLoaded', loadQuestions);
