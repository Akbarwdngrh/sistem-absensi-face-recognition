/* Absensi publik V2 — kamera, deteksi multi-wajah, umpan balik.
 *
 * Prinsip: klien hanya mengirim frame dan menampilkan hasil.
 * Identitas, skor, dan keputusan pencatatan SELALU ditentukan backend.
 */
(function () {
  'use strict';

  var video = document.getElementById('video');
  var canvas = document.getElementById('overlay');
  var overlayMsg = document.getElementById('overlayMsg');
  var startBtn = document.getElementById('startBtn');
  var stopBtn = document.getElementById('stopBtn');
  var camStatus = document.getElementById('camStatus');
  var camStatusText = document.getElementById('camStatusText');
  var faceCount = document.getElementById('faceCount');
  var recordedCount = document.getElementById('recordedCount');
  var frameCount = document.getElementById('frameCount');
  var faceList = document.getElementById('faceList');
  var sessionSelect = document.getElementById('sessionSelect');
  var engineInfo = document.getElementById('engineInfo');

  if (!video || !startBtn) { return; }

  var INTERVAL_MS = 1500;      // jeda antar frame yang dikirim ke server
  var CAPTURE_MAX = 800;       // sisi terpanjang frame yang dikirim (px)
  var MAX_TRACKED = 12;

  var stream = null;
  var running = false;
  var inFlight = false;
  var timer = null;
  var frames = 0;
  var recorded = 0;
  var ctx = canvas.getContext('2d');
  var grabCanvas = document.createElement('canvas');
  var lastBoxes = [];

  var csrfMeta = document.querySelector('meta[name="csrf-public"]');
  var CSRF = csrfMeta ? csrfMeta.getAttribute('content') : '';

  function setStatus(kind, text) {
    camStatus.className = 'cam-status' + (kind ? ' ' + kind : '');
    camStatusText.textContent = text;
  }

  function setCounts(faceN, recN, frameN) {
    if (faceCount) faceCount.textContent = faceN;
    if (recordedCount) recordedCount.textContent = recN;
    if (frameCount) frameCount.textContent = frameN;
  }

  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function renderFaces(faces, pesan) {
    if (!faceList) { return; }
    if (!faces || !faces.length) {
      faceList.innerHTML = pesan
        ? '<div class="face-item warn"><span class="who">' + esc(pesan) + '</span></div>'
        : '';
      return;
    }
    var html = '';
    for (var i = 0; i < faces.length; i++) {
      var f = faces[i];
      var cls = 'face-item';
      var label = 'Wajah tidak dikenal';
      if (f.status === 'tercatat') { cls += ' ok'; label = f.nama + ' — tercatat'; }
      else if (f.status === 'sudah_tercatat') { cls += ' ok'; label = f.nama + ' — sudah tercatat'; }
      else if (f.status === 'tidak_anggota') { cls += ' warn'; label = f.nama + ' — bukan anggota kelas'; }
      else if (f.status === 'belum_terdaftar') { cls += ' warn'; label = f.nama + ' — belum terdaftar'; }
      else { cls += ' bad'; }
      var skor = (f.similarity !== null && f.similarity !== undefined)
        ? ' · kemiripan ' + Number(f.similarity).toFixed(3) : '';
      html += '<div class="' + cls + '"><span class="who">' + esc(label) + '</span>' +
        '<span class="small muted">' + esc(f.message || '') + esc(skor) + '</span></div>';
    }
    faceList.innerHTML = html;
  }

  function drawOverlay(boxes) {
    if (!canvas || !video) { return; }
    var vw = video.videoWidth || 640;
    var vh = video.videoHeight || 480;
    if (canvas.width !== vw || canvas.height !== vh) {
      canvas.width = vw;
      canvas.height = vh;
    }
    ctx.clearRect(0, 0, vw, vh);
    if (!boxes || !boxes.length) { return; }
    for (var i = 0; i < boxes.length; i++) {
      var b = boxes[i];
      var x = b[0], y = b[1], w = b[2] - b[0], h = b[3] - b[1];
      var ok = b.recognized;
      ctx.strokeStyle = ok ? 'rgba(52, 211, 153, .95)' : 'rgba(251, 191, 36, .9)';
      ctx.lineWidth = Math.max(2, vw / 260);
      ctx.strokeRect(x, y, w, h);
      ctx.fillStyle = ok ? 'rgba(52, 211, 153, .16)' : 'rgba(251, 191, 36, .12)';
      ctx.fillRect(x, y, w, h);
    }
  }

  function grabFrame() {
    var w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) { return null; }
    var scale = Math.min(1, CAPTURE_MAX / Math.max(w, h));
    grabCanvas.width = Math.round(w * scale);
    grabCanvas.height = Math.round(h * scale);
    var g = grabCanvas.getContext('2d');
    g.drawImage(video, 0, 0, grabCanvas.width, grabCanvas.height);
    return grabCanvas;
  }

  function toBlob() {
    return new Promise(function (resolve) {
      grabCanvas.toBlob(function (b) { resolve(b); }, 'image/jpeg', 0.82);
    });
  }

  function stopStream() {
    if (timer) { window.clearTimeout(timer); timer = null; }
    if (stream) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
    }
    video.classList.remove('on');
    if (overlayMsg) { overlayMsg.hidden = false; }
  }

  function scheduleNext(delay) {
    if (!running) { return; }
    timer = window.setTimeout(captureAndSend, delay === undefined ? INTERVAL_MS : delay);
  }

  async function captureAndSend() {
    if (!running || inFlight) { return; }
    var frame = grabFrame();
    if (!frame) { scheduleNext(); return; }

    inFlight = true;
    frames += 1;
    setStatus('busy', 'Memproses wajah…');

    try {
      var blob = await toBlob();
      if (!blob) { inFlight = false; scheduleNext(); return; }

      var fd = new FormData();
      fd.append('session_id', sessionSelect ? sessionSelect.value : '');
      fd.append('csrf', CSRF);
      fd.append('image', blob, 'frame.jpg');

      var res = await fetch('/api/public/attendance/frame', {
        method: 'POST', body: fd, credentials: 'same-origin'
      });
      var data = await res.json();
      inFlight = false;

      if (!data.ok) {
        setStatus('error', data.message || 'Permintaan gagal.');
        if (data.reason === 'session_closed' || data.reason === 'session_not_found') {
          running = false;
          stopStream();
        }
        scheduleNext(3000);
        return;
      }

      var faces = data.faces || [];
      lastBoxes = faces.map(function (f) {
        return [f.box[0], f.box[1], f.box[2], f.box[3], f.recognized];
      });
      drawOverlay(lastBoxes);
      setCounts(faces.length, recorded, frames);
      renderFaces(faces, data.pesan);
      setStatus('ready', data.pesan || 'Kamera aktif.');

      if (data.n_tercatat) {
        recorded += data.n_tercatat;
        setCounts(faces.length, recorded, frames);
      }
      scheduleNext();
    } catch (err) {
      inFlight = false;
      setStatus('error', 'Koneksi ke server bermasalah. Mencoba lagi…');
      scheduleNext(3000);
    }
  }

  function cameraErrorMessage(err) {
    var name = (err && err.name) || '';
    if (name === 'unsupported' || !window.isSecureContext) {
      return 'Kamera memerlukan koneksi aman (HTTPS) atau localhost, dan browser yang mendukung getUserMedia.';
    }
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError' || name === 'SecurityError') {
      return 'Izin kamera ditolak. Aktifkan izin kamera di pengaturan browser, lalu muat ulang halaman.';
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
      return 'Kamera tidak ditemukan pada perangkat ini.';
    }
    if (name === 'NotReadableError' || name === 'TrackStartError') {
      return 'Kamera sedang dipakai aplikasi lain. Tutup aplikasi tersebut lalu coba lagi.';
    }
    return 'Tidak dapat mengakses kamera. Periksa izin dan perangkat kamera Anda.';
  }

  async function getMedia() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw { name: 'unsupported' };
    }
    var attempts = [
      { video: { facingMode: 'user', width: { ideal: 960 }, height: { ideal: 720 } }, audio: false },
      { video: { facingMode: 'user' }, audio: false },
      { video: true, audio: false }
    ];
    var lastErr = null;
    for (var i = 0; i < attempts.length; i++) {
      try {
        return await navigator.mediaDevices.getUserMedia(attempts[i]);
      } catch (err) {
        lastErr = err;
        if (err && (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError')) { throw err; }
      }
    }
    throw lastErr || { name: 'unsupported' };
  }

  async function start() {
    if (running) { return; }
    startBtn.disabled = true;
    setStatus('busy', 'Meminta izin kamera…');
    try {
      stream = await getMedia();
    } catch (err) {
      setStatus('error', cameraErrorMessage(err));
      renderFaces([], cameraErrorMessage(err));
      startBtn.disabled = false;
      return;
    }
    video.srcObject = stream;
    video.classList.add('on');
    try { await video.play(); } catch (e) { /* autoplay diblokir */ }
    if (overlayMsg) { overlayMsg.hidden = true; }
    stopBtn.disabled = false;
    running = true;
    setStatus('ready', 'Kamera aktif. Berdiri di depan kamera.');
    scheduleNext(700);
  }

  function stop() {
    running = false;
    stopStream();
    startBtn.disabled = false;
    stopBtn.disabled = true;
    setStatus('', 'Kamera dihentikan.');
    drawOverlay([]);
  }

  startBtn.addEventListener('click', start);
  stopBtn.addEventListener('click', stop);
  window.addEventListener('pagehide', stop);
  document.addEventListener('visibilitychange', function () {
    if (document.hidden && running) { stop(); }
  });

  if (sessionSelect) {
    sessionSelect.addEventListener('change', function () {
      if (running) { stop(); }
      renderFaces([], '');
    });
  }

  /* Info mesin (ambang & jumlah identitas) — bukan data biometrik. */
  fetch('/api/auth/engine', { credentials: 'same-origin' })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!engineInfo) { return; }
      engineInfo.textContent = d.ready
        ? ('Model siap · device ' + d.device + ' · ' + d.persons + ' identitas · ambang ' +
           Number(d.threshold).toFixed(3) + ' · maksimal ' + d.max_faces_per_frame + ' wajah per frame')
        : ('Mesin pengenalan wajah belum siap' + (d.error ? ' (' + d.error + ')' : ''));
    })
    .catch(function () { /* abaikan */ });
})();
