// 在页面上显示一个能量条，帮助调试 VAD 阈值

export class EnergyMeter {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    if (!this.container) return;

    this.container.innerHTML = `
      <div style="font-size:12px; color:#666; margin-bottom:4px;">
        能量计（dBFS）
      </div>
      <div style="background:#eee; height:20px; border-radius:4px; overflow:hidden;">
        <div id="energy-bar" style="
          height:100%;
          background:#4CAF50;
          transition:width 0.05s;
          width:0%;
        "></div>
      </div>
      <div id="energy-value" style="font-size:11px; color:#999; margin-top:2px;">
        -∞ dBFS
      </div>
    `;
    this.bar = document.getElementById('energy-bar');
    this.valueEl = document.getElementById('energy-value');
  }

  update(energyDB) {
    if (!this.bar) return;

    // 把 -80dBFS 到 0dBFS 映射到 0% 到 100%
    const minDB = -80, maxDB = 0;
    const percent = Math.max(0, Math.min(100,
      ((energyDB - minDB) / (maxDB - minDB)) * 100
    ));

    this.bar.style.width = `${percent}%`;
    this.bar.style.background = energyDB > -35 ? '#f44336' : '#4CAF50';
    this.valueEl.textContent = `${energyDB.toFixed(1)} dBFS`;
  }
}
