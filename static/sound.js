let audioCtx = null;
let soundMuted = false;
try { soundMuted = localStorage.getItem('campusEscapeMuted') === 'true'; } catch (error) { /* Storage can be disabled by browser privacy settings. */ }

function syncAudioToggle() {
    document.querySelectorAll('[data-audio-toggle]').forEach(button => {
        button.textContent = soundMuted ? '🔇 Sound off' : '🔊 Sound on';
        button.setAttribute('aria-pressed', String(!soundMuted));
    });
}

function toggleAudio() {
    soundMuted = !soundMuted;
    try { localStorage.setItem('campusEscapeMuted', String(soundMuted)); } catch (error) { /* The toggle still works for this page. */ }
    syncAudioToggle();
}

function playSfx(type) {
    if (soundMuted) return;
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    if (!audioCtx) audioCtx = new AudioContextClass();
    if (audioCtx.state === 'suspended') {
        audioCtx.resume();
    }
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    
    if (type === 'click') {
        osc.frequency.setValueAtTime(450, audioCtx.currentTime);
        gain.gain.setValueAtTime(0.05, audioCtx.currentTime);
        osc.start();
        osc.stop(audioCtx.currentTime + 0.05);
    } else if (type === 'correct') {
        osc.frequency.setValueAtTime(523.25, audioCtx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(659.25, audioCtx.currentTime + 0.15);
        gain.gain.setValueAtTime(0.1, audioCtx.currentTime);
        osc.start();
        osc.stop(audioCtx.currentTime + 0.15);
    } else if (type === 'wrong') {
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(180, audioCtx.currentTime);
        gain.gain.setValueAtTime(0.1, audioCtx.currentTime);
        osc.start();
        osc.stop(audioCtx.currentTime + 0.2);
    }
}

document.addEventListener('DOMContentLoaded', syncAudioToggle);
