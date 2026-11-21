/* Versioned, synthetic website concept. No database requests or machine actions. */
(async () => {
  const root = document.getElementById('demo-root');
  const select = document.getElementById('demo-scenario');
  try {
    const response = await fetch('demo/investigation-v1.json');
    if (!response.ok) throw new Error('Fixture unavailable');
    const fixture = await response.json();
    if (fixture.fixture_version !== 1 || fixture.synthetic !== true) throw new Error('Unsupported fixture');
    const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    function plot(values, label, secondary = false) {
      if (!values) return '<div class="demo-empty">Waveform unavailable<br><span>Absence of evidence ≠ no previous event</span></div>';
      const d = values.map((v, i) => `${i ? 'L' : 'M'}${(i * 600 / (values.length - 1)).toFixed(2)},${(70 - v * 48).toFixed(2)}`).join(' ');
      return `<svg viewBox="0 0 600 140" role="img" aria-label="${escape(label)}"><path d="M0 35H600 M0 70H600 M0 105H600" stroke="#24302e" fill="none"/><path d="${d}" fill="none" stroke="${secondary ? '#89b9dc' : '#a8df73'}" stroke-width="1.7" vector-effect="non-scaling-stroke"/></svg>`;
    }
    function render() {
      const incident = fixture.incident, item = fixture.scenarios[select.value];
      root.innerHTML = `<div class="demo-state" role="status"><span class="demo-status-dot"></span>${escape(item.state)}<span class="demo-source">FIXTURE v${fixture.fixture_version} / NO LIVE QUERY</span></div>
      <div class="demo-wave-grid"><article class="demo-wave"><span class="demo-kicker">01 / CURRENT INCIDENT</span><h3>${escape(incident.asset)}</h3><p>${escape(incident.context)}</p>${plot(incident.plot, 'Synthetic current vibration signal')}<div class="demo-reference">${escape(incident.recording_id)}<br>Samples [${incident.start_sample}, ${incident.end_sample}) · 12 kHz · g</div></article>
      <article class="demo-wave"><span class="demo-kicker">02 / HISTORICAL EVIDENCE</span><h3>${escape(item.title)}</h3><p>${escape(item.context)}</p>${plot(item.plot, 'Synthetic historical vibration signal', true)}<div class="demo-reference">${escape(item.recording_id)}<br>Samples [${item.start_sample}, ${item.end_sample}) · illustrative reference</div></article></div>
      <div class="demo-findings"><article><span class="demo-kicker">03 / WHAT THE EVIDENCE SUPPORTS</span><h3>${select.value === 'precedent' ? 'A starting point for investigation.' : select.value === 'lookalike' ? 'Check applicability first.' : 'Keep the evidence gap visible.'}</h3><p>${escape(item.assessment)}</p></article><article><span class="demo-kicker">04 / LESSON WORKFLOW · PLANNED</span><h3>What was tried. What was observed.</h3><p>${escape(item.lesson)}</p><span class="demo-record">${escape(item.work_order)} · fictional maintenance record</span></article></div>
      <details class="demo-details"><summary>Inspect this case’s machine-readable evidence</summary><pre></pre></details>`;
      root.querySelector('pre').textContent = JSON.stringify({fixture_version: fixture.fixture_version, synthetic: true, query: {recording_id:incident.recording_id,start_sample:incident.start_sample,end_sample:incident.end_sample}, evidence_states: item.evidence_states, historical_recording: item.recording_id, work_order: item.work_order, limits:['No live search or measured similarity score','Synthetic outcome, not a verified engineering lesson','Plot points are illustrative, not raw recording samples']}, null, 2);
    }
    select.addEventListener('change', render);
    render();
  } catch (error) {
    root.textContent = 'The illustrative fixture could not be loaded. Use the download link above, or serve this page through a local HTTP server.';
    select.disabled = true;
  }
})();
