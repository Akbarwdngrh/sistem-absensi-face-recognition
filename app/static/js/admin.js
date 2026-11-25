/* Admin V2 — dashboard berbasis fetch ke /api/admin/*.
 * Semua mutasi mengirim header X-CSRF-Token; server menolak tanpa itu.
 */
(function () {
  'use strict';

  var CFG = window.ABSENSI_V2 || {};
  var CSRF = CFG.csrf || (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function api(path, options) {
    options = options || {};
    var headers = { 'Accept': 'application/json' };
    if (options.body && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }
    if (options.method && options.method !== 'GET') {
      headers['X-CSRF-Token'] = CSRF;
    }
    return fetch(path, {
      method: options.method || 'GET',
      headers: headers,
      body: options.body,
      credentials: 'same-origin'
    }).then(function (res) {
      var ct = res.headers.get('content-type') || '';
      if (ct.indexOf('application/json') === -1) {
        return res.text().then(function (t) { return { ok: res.ok, text: t }; });
      }
      return res.json().then(function (d) { return { ok: res.ok, data: d }; });
    });
  }

  function toast(msg, kind) {
    var el = document.createElement('div');
    el.className = 'alert ' + (kind || 'ok');
    el.textContent = msg;
    el.style.position = 'fixed';
    el.style.bottom = '1rem';
    el.style.left = '1rem';
    el.style.right = '1rem';
    el.style.zIndex = '80';
    el.style.boxShadow = 'var(--shadow)';
    document.body.appendChild(el);
    window.setTimeout(function () { el.remove(); }, 4200);
  }

  function fillBody(tableId, rows, mapper) {
    var tbody = document.querySelector('#' + tableId + ' tbody');
    if (!tbody) { return; }
    if (!rows || !rows.length) {
      tbody.innerHTML = '<tr><td colspan="12" class="muted">Belum ada data.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(mapper).join('');
  }

  /* ------------------------------------------------------------- tabs */
  var tabs = document.querySelectorAll('.tab');
  Array.prototype.forEach.call(tabs, function (tab) {
    tab.addEventListener('click', function () {
      Array.prototype.forEach.call(tabs, function (t) { t.classList.remove('is-active'); });
      tab.classList.add('is-active');
      Array.prototype.forEach.call(document.querySelectorAll('.tab-panel'), function (p) {
        p.classList.remove('is-active');
      });
      var panel = document.getElementById('panel-' + tab.dataset.tab);
      if (panel) { panel.classList.add('is-active'); }
      if (tab.dataset.tab === 'mhs') { loadMahasiswa(); }
      if (tab.dataset.tab === 'dosen') { loadDosen(); }
      if (tab.dataset.tab === 'kelas') { loadKelas(); loadDosenOptions(); }
      if (tab.dataset.tab === 'sesi') { loadSesi(); loadKelasOptions(); }
      if (tab.dataset.tab === 'hadir') { loadKehadiran(); loadKelasOptions(); }
      if (tab.dataset.tab === 'audit') { loadAudit(); }
    });
  });

  /* -------------------------------------------------------- mahasiswa */
  var mhsPage = 1;

  function loadMahasiswa() {
    var q = (document.getElementById('mhs-search') || {}).value || '';
    api('/api/admin/mahasiswa?page=' + mhsPage + '&q=' + encodeURIComponent(q))
      .then(function (r) {
        if (!r.ok) { toast('Gagal memuat data mahasiswa.', 'bad'); return; }
        var d = r.data;
        fillBody('tbl-mhs', d.rows, function (m) {
          return '<tr>' +
            '<td class="mono">' + esc(m.nim) + '</td>' +
            '<td>' + esc(m.nama) + '</td>' +
            '<td>' + m.n_kelas + '</td>' +
            '<td>' + (m.n_wajah ? m.n_wajah + ' terdaftar' : '<span class="muted">belum</span>') + '</td>' +
            '<td class="row">' +
              '<label class="check"><input type="file" accept="image/*" data-enroll="' + m.id + '"> daftarkan wajah</label>' +
              '<button class="btn btn-sm btn-danger" data-del-mhs="' + m.id + '" type="button">Hapus</button>' +
            '</td></tr>';
        });
        document.getElementById('mhs-page').textContent =
          'hal. ' + d.page + ' / total ' + d.total;
      });
  }

  document.getElementById('mhs-refresh')?.addEventListener('click', function () {
    mhsPage = 1; loadMahasiswa();
  });
  document.getElementById('mhs-search')?.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { mhsPage = 1; loadMahasiswa(); }
  });
  document.querySelector('[data-page-mhs="prev"]')?.addEventListener('click', function () {
    if (mhsPage > 1) { mhsPage -= 1; loadMahasiswa(); }
  });
  document.querySelector('[data-page-mhs="next"]')?.addEventListener('click', function () {
    mhsPage += 1; loadMahasiswa();
  });

  var formMhs = document.getElementById('form-mhs');
  if (formMhs) {
    formMhs.addEventListener('submit', function (e) {
      e.preventDefault();
      var payload = {
        nim: document.getElementById('mhs-nim').value,
        nama: document.getElementById('mhs-nama').value,
        email: document.getElementById('mhs-email').value || null
      };
      api('/api/admin/mahasiswa', { method: 'POST', body: JSON.stringify(payload) })
        .then(function (r) {
          if (!r.ok) { toast(r.data.detail || 'Gagal menyimpan mahasiswa.', 'bad'); return; }
          toast('Mahasiswa disimpan.');
          formMhs.reset();
          loadMahasiswa();
        });
    });
  }

  document.addEventListener('click', function (e) {
    var delBtn = e.target.closest('[data-del-mhs]');
    if (delBtn) {
      var id = delBtn.getAttribute('data-del-mhs');
      if (!window.confirm('Hapus mahasiswa beserta data absensinya? Tindakan ini tercatat di audit.')) { return; }
      api('/api/admin/mahasiswa/' + id, { method: 'DELETE' }).then(function (r) {
        if (!r.ok) { toast(r.data.detail || 'Gagal menghapus.', 'bad'); return; }
        toast('Mahasiswa dihapus.');
        loadMahasiswa();
      });
      return;
    }
  });

  document.addEventListener('change', function (e) {
    var input = e.target.closest('[data-enroll]');
    if (!input || !input.files || !input.files.length) { return; }
    var mhsId = input.getAttribute('data-enroll');
    var fd = new FormData();
    fd.append('image', input.files[0], 'wajah.jpg');
    api('/api/admin/mahasiswa/' + mhsId + '/wajah', { method: 'POST', body: fd })
      .then(function (r) {
        if (!r.ok) { toast(r.data.detail || 'Gagal mendaftarkan wajah.', 'bad'); return; }
        toast('Wajah terdaftar untuk mahasiswa ini.');
        input.value = '';
        loadMahasiswa();
      });
  });

  /* ------------------------------------------------------------ dosen */
  function loadDosen() {
    api('/api/admin/dosen').then(function (r) {
      if (!r.ok) { toast('Gagal memuat data dosen.', 'bad'); return; }
      fillBody('tbl-dosen', r.data.rows, function (d) {
        return '<tr>' +
          '<td>' + esc(d.nama) + '</td>' +
          '<td class="mono">' + esc(d.nid || '-') + '</td>' +
          '<td>' + esc(d.email || '-') + '</td>' +
          '<td class="mono small">' + esc(d.username || 'tanpa akun') + '</td>' +
          '<td class="row">' +
            '<button class="btn btn-sm" data-reset-pw="' + d.id + '" type="button">Reset sandi</button>' +
            '<button class="btn btn-sm btn-danger" data-del-dosen="' + d.id + '" type="button">Hapus</button>' +
          '</td></tr>';
      });
    });
  }

  var formDosen = document.getElementById('form-dosen');
  if (formDosen) {
    var cekAkun = document.getElementById('dsn-akun');
    var fieldsAkun = document.getElementById('dsn-akun-fields');
    cekAkun.addEventListener('change', function () {
      fieldsAkun.style.display = cekAkun.checked ? '' : 'none';
    });
    formDosen.addEventListener('submit', function (e) {
      e.preventDefault();
      var payload = {
        nama: document.getElementById('dsn-nama').value,
        nid: document.getElementById('dsn-nid').value || null,
        email: document.getElementById('dsn-email').value || null,
        buat_akun: cekAkun.checked,
        username: cekAkun.checked ? document.getElementById('dsn-username').value : null,
        password: cekAkun.checked ? document.getElementById('dsn-password').value : null
      };
      api('/api/admin/dosen', { method: 'POST', body: JSON.stringify(payload) })
        .then(function (r) {
          if (!r.ok) { toast(r.data.detail || 'Gagal menyimpan dosen.', 'bad'); return; }
          toast('Dosen disimpan.');
          formDosen.reset();
          loadDosen();
        });
    });
  }

  document.addEventListener('click', function (e) {
    var del = e.target.closest('[data-del-dosen]');
    if (del) {
      var id = del.getAttribute('data-del-dosen');
      if (!window.confirm('Hapus dosen ini beserta akun loginya?')) { return; }
      api('/api/admin/dosen/' + id, { method: 'DELETE' }).then(function (r) {
        if (!r.ok) { toast(r.data.detail || 'Gagal menghapus.', 'bad'); return; }
        toast('Dosen dihapus.');
        loadDosen();
      });
      return;
    }
    var rst = e.target.closest('[data-reset-pw]');
    if (rst) {
      var pw = window.prompt('Kata sandi baru (minimal 10 karakter):');
      if (!pw) { return; }
      api('/api/admin/dosen/' + rst.getAttribute('data-reset-pw') + '/password',
        { method: 'POST', body: JSON.stringify({ password: pw }) })
        .then(function (r) {
          if (!r.ok) { toast(r.data.detail || 'Gagal menyetel kata sandi.', 'bad'); return; }
          toast('Kata sandi diperbarui; sesi login dosen diakhiri.');
        });
    }
  });

  /* ------------------------------------------------------------ kelas */
  var currentClassId = '';

  function loadKelas() {
    api('/api/admin/kelas').then(function (r) {
      if (!r.ok) { toast('Gagal memuat kelas.', 'bad'); return; }
      fillBody('tbl-kelas', r.data.rows, function (k) {
        return '<tr>' +
          '<td><strong>' + esc(k.label) + '</strong><div class="small muted">' + esc(k.kode_mk) + '</div></td>' +
          '<td>' + esc(k.nama_mk) + '</td>' +
          '<td>' + esc(k.dosen_nama || '-') + '</td>' +
          '<td>' + k.n_mhs + '</td>' +
          '<td class="row">' +
            '<button class="btn btn-sm" data-kelas-anggota="' + k.id + '" data-label="' + esc(k.label) + '" type="button">Anggota</button>' +
            '<button class="btn btn-sm btn-danger" data-del-kelas="' + k.id + '" type="button">Hapus</button>' +
          '</td></tr>';
      });
    });
  }

  function loadDosenOptions() {
    api('/api/admin/dosen?per_page=100').then(function (r) {
      if (!r.ok) { return; }
      var sel = document.getElementById('kls-dosen');
      if (!sel) { return; }
      var current = sel.value;
      sel.innerHTML = '<option value="">— belum ditentukan —</option>' +
        r.data.rows.map(function (d) {
          return '<option value="' + d.id + '">' + esc(d.nama) + '</option>';
        }).join('');
      sel.value = current;
    });
  }

  function loadKelasOptions() {
    api('/api/admin/kelas?per_page=100').then(function (r) {
      if (!r.ok) { return; }
      var opts = r.data.rows.map(function (k) {
        return '<option value="' + k.id + '">' + esc(k.label + ' — ' + k.nama_mk) + '</option>';
      }).join('');
      var s1 = document.getElementById('sesi-kelas');
      if (s1) { s1.innerHTML = opts; }
      var s2 = document.getElementById('h-class');
      if (s2) {
        s2.innerHTML = '<option value="">Semua kelas</option>' + opts;
      }
    });
  }

  var formKelas = document.getElementById('form-kelas');
  if (formKelas) {
    formKelas.addEventListener('submit', function (e) {
      e.preventDefault();
      var dosenId = document.getElementById('kls-dosen').value;
      var payload = {
        kode_mk: document.getElementById('kls-kode').value,
        nama_mk: document.getElementById('kls-nama').value,
        label: document.getElementById('kls-label').value,
        dosen_id: dosenId ? parseInt(dosenId, 10) : null,
        tahun_akademik: document.getElementById('kls-ta').value || null,
        semester: document.getElementById('kls-sem').value || null
      };
      api('/api/admin/kelas', { method: 'POST', body: JSON.stringify(payload) })
        .then(function (r) {
          if (!r.ok) { toast(r.data.detail || 'Gagal menyimpan kelas.', 'bad'); return; }
          toast('Kelas disimpan.');
          formKelas.reset();
          loadKelas();
        });
    });
  }

  document.addEventListener('click', function (e) {
    var del = e.target.closest('[data-del-kelas]');
    if (del) {
      if (!window.confirm('Hapus kelas ini? Semua sesi dan catatan absensinya ikut terhapus.')) { return; }
      api('/api/admin/kelas/' + del.getAttribute('data-del-kelas'), { method: 'DELETE' })
        .then(function (r) {
          if (!r.ok) { toast(r.data.detail || 'Gagal menghapus.', 'bad'); return; }
          toast('Kelas dihapus.');
          loadKelas();
        });
      return;
    }
    var Membership = e.target.closest('[data-kelas-anggota]');
    if (membership) {
      openAnggota(membership.getAttribute('data-kelas-anggota'),
        membership.getAttribute('data-label'));
    }
  });

  function openAnggota(classId, label) {
    currentClassId = classId;
    var wrap = document.getElementById('kelas-anggota-wrap');
    wrap.hidden = false;
    document.getElementById('kelas-anggota-title').textContent = 'Anggota kelas ' + label;
    api('/api/admin/kelas/' + classId).then(function (r) {
      if (!r.ok) { return; }
      var list = document.getElementById('kelas-anggota-list');
      list.innerHTML = r.data.anggota.length
        ? r.data.anggota.map(function (m) {
          return '<li>' + esc(m.nama) + ' · ' + esc(m.nim) +
            ' <button class="btn btn-sm" type="button" data-lepas="' + m.id + '" data-kelas="' +
            classId + '">lepas</button></li>';
        }).join('')
        : '<li class="muted">Belum ada anggota.</li>';

      api('/api/admin/mahasiswa?per_page=200').then(function (mr) {
        if (!mr.ok) { return; }
        var sudah = {};
        r.data.anggota.forEach(function (m) { sudah[m.id] = 1; });
        var pilih = document.getElementById('kelas-anggota-pilih');
        pilih.innerHTML = '<option value="">— pilih mahasiswa —</option>' +
          mr.data.rows.filter(function (m) { return !sudah[m.id]; })
            .map(function (m) {
              return '<option value="' + m.id + '">' + esc(m.nama + ' — ' + m.nim) + '</option>';
            }).join('');
      });
    });
  }

  document.getElementById('kelas-anggota-tambah')?.addEventListener('click', function () {
    var pilih = document.getElementById('kelas-anggota-pilih');
    var mhsId = pilih.value;
    if (!mhsId || !currentClassId) { return; }
    var classId = currentClassId;
    var fd = new FormData();
    fd.append('class_id', classId);
    fd.append('aktif', 'true');
    api('/api/admin/mahasiswa/' + mhsId + '/kelas', { method: 'POST', body: fd })
      .then(function (r) {
        if (!r.ok) { toast(r.data.detail || 'Gagal menambah anggota.', 'bad'); return; }
        toast('Anggota ditambahkan.');
        var title = document.getElementById('kelas-anggota-title').textContent.replace('Anggota kelas ', '');
        openAnggota(classId, title);
        loadKelas();
      });
  });

  document.addEventListener('click', function (e) {
    var lepas = e.target.closest('[data-lepas]');
    if (!lepas) { return; }
    var classId = lepas.getAttribute('data-kelas');
    var mhsId = lepas.getAttribute('data-lepas');
    var fd = new FormData();
    fd.append('class_id', classId);
    fd.append('aktif', 'false');
    api('/api/admin/mahasiswa/' + mhsId + '/kelas', { method: 'POST', body: fd })
      .then(function (r) {
        if (!r.ok) { toast(r.data.detail || 'Gagal melepas anggota.', 'bad'); return; }
        toast('Anggota dilepas dari kelas.');
        openAnggota(classId, '');
        loadKelas();
      });
  });

  /* ------------------------------------------------------------- sesi */
  function loadSesi() {
    api('/api/admin/sesi').then(function (r) {
      if (!r.ok) { toast('Gagal memuat sesi.', 'bad'); return; }
      fillBody('tbl-sesi', r.data.rows, function (s) {
        var aksi = s.status === 'buka'
          ? '<button class="btn btn-sm" data-tutup-sesi="' + s.id + '" type="button">Tutup</button>'
          : '<button class="btn btn-sm" data-buka-sesi="' + s.id + '" type="button">Buka lagi</button>';
        return '<tr>' +
          '<td>' + esc(s.label) + '</td>' +
          '<td>' + esc(s.tanggal) + '</td>' +
          '<td class="mono small">' + esc(String(s.mulai_at).slice(11, 16)) + '</td>' +
          '<td>' + s.n_hadir + ' / ' + s.n_anggota + '</td>' +
          '<td><span class="badge ' + (s.status === 'buka' ? 'hadir' : 'netral') + '">' +
            esc(s.status) + '</span></td>' +
          '<td>' + aksi + '</td></tr>';
      });
    });
  }

  var formSesi = document.getElementById('form-sesi');
  if (formSesi) {
    formSesi.addEventListener('submit', function (e) {
      e.preventDefault();
      var payload = {
        class_id: parseInt(document.getElementById('sesi-kelas').value, 10),
        tanggal: document.getElementById('sesi-tanggal').value,
        mulai_jam: document.getElementById('sesi-mulai').value,
        selesai_jam: document.getElementById('sesi-selesai').value || null,
        keterangan: document.getElementById('sesi-ket').value || null
      };
      api('/api/admin/sesi', { method: 'POST', body: JSON.stringify(payload) })
        .then(function (r) {
          if (!r.ok) { toast(r.data.detail || 'Gagal membuka sesi.', 'bad'); return; }
          toast('Sesi absensi dibuka.');
          formSesi.reset();
          loadSesi();
        });
    });
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-tutup-sesi]');
    if (t) {
      api('/api/admin/sesi/' + t.getAttribute('data-tutup-sesi') + '/tutup', { method: 'POST' })
        .then(function (r) {
          if (!r.ok) { toast('Gagal menutup sesi.', 'bad'); return; }
          toast('Sesi ditutup.');
          loadSesi();
        });
      return;
    }
    var b = e.target.closest('[data-buka-sesi]');
    if (b) {
      api('/api/admin/sesi/' + b.getAttribute('data-buka-sesi') + '/buka', { method: 'POST' })
        .then(function (r) {
          if (!r.ok) { toast('Gagal membuka sesi.', 'bad'); return; }
          toast('Sesi dibuka kembali.');
          loadSesi();
        });
    }
  });

  /* -------------------------------------------------------- kehadiran */
  var hPage = 1;

  function loadKehadiran() {
    var classId = document.getElementById('h-class').value;
    var tanggal = document.getElementById('h-tanggal').value;
    var status = document.getElementById('h-status').value;
    var q = document.getElementById('h-q').value;
    var url = '/api/admin/kehadiran?page=' + hPage +
      '&class_id=' + encodeURIComponent(classId) +
      '&tanggal=' + encodeURIComponent(tanggal) +
      '&status=' + encodeURIComponent(status) +
      '&q=' + encodeURIComponent(q);
    api(url).then(function (r) {
      if (!r.ok) { toast('Gagal memuat kehadiran.', 'bad'); return; }
      fillBody('tbl-hadir', r.data.rows, function (h) {
        var skor = h.similarity === null || h.similarity === undefined
          ? '-' : Number(h.similarity).toFixed(4);
        return '<tr>' +
          '<td class="mono small">' + esc(h.recorded_at) + '</td>' +
          '<td class="mono">' + esc(h.nim) + '</td>' +
          '<td>' + esc(h.nama) + '</td>' +
          '<td>' + esc(h.kelas) + '</td>' +
          '<td><span class="badge ' + (h.status === 'HADIR' ? 'hadir' : 'netral') + '">' +
            esc(h.status) + '</span></td>' +
          '<td class="mono small">' + skor + '</td>' +
          '<td class="small">' + esc(h.metode) + '</td>' +
          '<td><button class="btn btn-sm" data-koreksi="' + h.id + '" type="button">Koreksi</button></td>' +
          '</tr>';
      });
      document.getElementById('h-page').textContent = 'hal. ' + r.data.page + ' / total ' + r.data.total;
    });
  }

  document.getElementById('h-search')?.addEventListener('click', function () {
    hPage = 1; loadKehadiran();
  });
  document.querySelector('[data-page-h="prev"]')?.addEventListener('click', function () {
    if (hPage > 1) { hPage -= 1; loadKehadiran(); }
  });
  document.querySelector('[data-page-h="next"]')?.addEventListener('click', function () {
    hPage += 1; loadKehadiran();
  });

  function exportUrl(fmt) {
    var classId = document.getElementById('h-class').value;
    var tanggal = document.getElementById('h-tanggal').value;
    var status = document.getElementById('h-status').value;
    var q = document.getElementById('h-q').value;
    return '/api/admin/export?fmt=' + fmt +
      '&class_id=' + encodeURIComponent(classId) +
      '&tanggal=' + encodeURIComponent(tanggal) +
      '&status=' + encodeURIComponent(status) +
      '&q=' + encodeURIComponent(q);
  }
  document.getElementById('h-csv')?.addEventListener('click', function (e) {
    e.preventDefault(); window.location.href = exportUrl('csv');
  });
  document.getElementById('h-xlsx')?.addEventListener('click', function (e) {
    e.preventDefault(); window.location.href = exportUrl('xlsx');
  });

  document.addEventListener('click', function (e) {
    var k = e.target.closest('[data-koreksi]');
    if (!k) { return; }
    var id = k.getAttribute('data-koreksi');
    var status = window.prompt('Status baru (HADIR / TOLAK / MANUAL):', 'MANUAL');
    if (!status) { return; }
    var alasan = window.prompt('Alasan koreksi (wajib tercatat di audit):', '') || '';
    api('/api/admin/kehadiran/' + id, {
      method: 'PUT',
      body: JSON.stringify({ status: status.toUpperCase(), catatan: alasan })
    }).then(function (r) {
      if (!r.ok) { toast(r.data.detail || 'Gagal melakukan koreksi.', 'bad'); return; }
      toast('Koreksi tersimpan dan tercatat di audit.');
      loadKehadiran();
    });
  });

  /* ------------------------------------------------------------ audit */
  var aPage = 1;

  function loadAudit() {
    api('/api/admin/audit?page=' + aPage).then(function (r) {
      if (!r.ok) { toast('Gagal memuat audit.', 'bad'); return; }
      fillBody('tbl-audit', r.data.rows, function (a) {
        return '<tr>' +
          '<td class="mono small">' + esc(a.created_at) + '</td>' +
          '<td>' + esc(a.username || 'sistem') + '</td>' +
          '<td><code>' + esc(a.aksi) + '</code></td>' +
          '<td class="small">' + esc(a.entitas || '-') + (a.entitas_id ? ' #' + esc(a.entitas_id) : '') + '</td>' +
          '<td class="small muted">' + esc(a.detail || '') + '</td>' +
          '</tr>';
      });
      document.getElementById('a-page').textContent = 'hal. ' + r.data.page + ' / total ' + r.data.total;
    });
  }

  document.querySelector('[data-page-a="prev"]')?.addEventListener('click', function () {
    if (aPage > 1) { aPage -= 1; loadAudit(); }
  });
  document.querySelector('[data-page-a="next"]')?.addEventListener('click', function () {
    aPage += 1; loadAudit();
  });

  /* -------------------------------------------------------------- init */
  loadMahasiswa();
  loadDosen();
  loadKelas();
  loadDosenOptions();
  loadKelasOptions();
})();
