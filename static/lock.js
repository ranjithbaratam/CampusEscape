// Master Sequence Calculation:
// A = len("ESCAPE") = 6
// B = (5 % 3) = 2
// C = 32 (Sequence doubles: 2, 4, 8, 16, 32)
// MASTER KEY = (6 * 2) + 32 = 44

const CORRECT_KEY = "44";

function verifyKey(event) {
    // Prevent default form submission redirect
    event.preventDefault();

    const inputVal = document.getElementById("keyInput").value.trim();
    const modal = document.getElementById("responseModal");
    const card = document.getElementById("modalCard");
    const icon = document.getElementById("modalIcon");
    const title = document.getElementById("modalTitle");
    const desc = document.getElementById("modalDesc");
    const btn = document.getElementById("modalBtn");

    if (inputVal === CORRECT_KEY) {
        if (typeof playSfx === 'function') playSfx('correct');
        
        card.className = "modal-card success";
        icon.innerText = "🎉";
        title.innerText = "ACCESS GRANTED!";
        desc.innerHTML = "<strong>MASTER CIPHER CLEARED!</strong><br>You have successfully beaten all sectors and escaped Campus Escape!";
        btn.className = "btn-modal success";
        btn.innerText = "RETURN TO MAP →";
        btn.onclick = () => { window.location.href = "/map"; };
    } else {
        if (typeof playSfx === 'function') playSfx('wrong');
        
        card.className = "modal-card err";
        icon.innerText = "⚠️";
        title.innerText = "INVALID MASTER KEY";
        desc.innerHTML = `Entered: <strong style="color:#f87171;">"${inputVal}"</strong><br>Calculation incorrect. Review the cipher formula: <code>(A * B) + C</code>.`;
        btn.className = "btn-modal err";
        btn.innerText = "TRY AGAIN";
        btn.onclick = () => { closeModal(); };
    }

    modal.style.display = "flex";
}

function closeModal() {
    document.getElementById("responseModal").style.display = "none";
    const input = document.getElementById("keyInput");
    input.value = "";
    input.focus();
}