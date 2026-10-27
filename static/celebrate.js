window.celebrateMilestone = function () {
    const canvas = document.createElement('canvas');
    canvas.setAttribute('aria-hidden', 'true');
    Object.assign(canvas.style, {position:'fixed',inset:'0',width:'100%',height:'100%',pointerEvents:'none',zIndex:'9999'});
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = window.innerWidth * ratio; canvas.height = window.innerHeight * ratio;
    ctx.scale(ratio, ratio);
    const colors = ['#38bdf8','#5eead4','#c084fc','#fbbf24','#fb7185'];
    const bits = Array.from({length:110}, () => ({x:Math.random()*innerWidth,y:-Math.random()*innerHeight*.5,
        vx:(Math.random()-.5)*4,vy:2+Math.random()*4,size:4+Math.random()*6,angle:Math.random()*6.2,spin:(Math.random()-.5)*.2,color:colors[Math.floor(Math.random()*colors.length)]}));
    let frame=0;
    function draw(){ctx.clearRect(0,0,innerWidth,innerHeight);bits.forEach(b=>{b.x+=b.vx;b.y+=b.vy;b.angle+=b.spin;b.vy+=.025;ctx.save();ctx.translate(b.x,b.y);ctx.rotate(b.angle);ctx.fillStyle=b.color;ctx.fillRect(-b.size/2,-b.size/2,b.size,b.size*.65);ctx.restore();});if(++frame<150)requestAnimationFrame(draw);else canvas.remove();}
    draw();
};
