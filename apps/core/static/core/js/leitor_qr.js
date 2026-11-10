/* Leitor de QR Code pela câmera. Uso: <button data-leitor-qr="ID_DO_INPUT">; ao ler, preenche o
   input, mostra uma animação de tela inteira e, no OK do usuário, envia o formulário dele. Usa BarcodeDetector quando o navegador tem; senão, jsQR. */
(function () {
    var botoes = document.querySelectorAll('[data-leitor-qr]');
    if (!botoes.length) return;

    var dlg = document.createElement('dialog');
    dlg.style.cssText = 'padding:1rem;border:0;border-radius:.5rem;width:min(95vw,32rem);max-width:95vw';
    dlg.innerHTML =
        '<p style="margin:0 0 .5rem;font-weight:600">Aponte a câmera para o QR Code do palete</p>' +
        '<div style="position:relative;background:#000;border-radius:.375rem;overflow:hidden">' +
        '<video playsinline muted style="display:block;width:100%;max-height:70vh"></video></div>' +
        '<p data-msg style="margin:.5rem 0 0;min-height:1.25rem;font-size:.875rem;color:#475569"></p>' +
        '<button type="button" class="btn btn-secondary" style="margin-top:.75rem;width:100%;min-height:3rem">Fechar</button>';
    document.body.appendChild(dlg);
    var video = dlg.querySelector('video'), msg = dlg.querySelector('[data-msg]');
    var canvas = document.createElement('canvas'), ctx = canvas.getContext('2d', { willReadFrequently: true });
    var stream = null, rodando = false, alvo = null, detector = null;

    function parar() {
        rodando = false;
        if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
        stream = null;
        video.srcObject = null;
    }
    dlg.querySelector('button').addEventListener('click', function () { dlg.close(); });
    dlg.addEventListener('close', parar);

    var estilo = document.createElement('style');
    estilo.textContent =
        '@keyframes qr-fundo{from{opacity:0}to{opacity:1}}' +
        '@keyframes qr-pulso{0%{transform:scale(.4);opacity:0}60%{transform:scale(1.12);opacity:1}100%{transform:scale(1)}}' +
        '@keyframes qr-onda{0%{transform:scale(1);opacity:.6}100%{transform:scale(2.2);opacity:0}}' +
        '@keyframes qr-traco{to{stroke-dashoffset:0}}' +
        '@keyframes qr-texto{from{transform:translateY(12px);opacity:0}to{transform:none;opacity:1}}' +
        '@media (prefers-reduced-motion:reduce){[data-qr-ok] *{animation-duration:.01ms!important;animation-delay:0s!important}}';
    document.head.appendChild(estilo);

    function confirmar(texto) {
        var campo = alvo;
        var ov = document.createElement('div');
        ov.setAttribute('data-qr-ok', '');
        ov.setAttribute('role', 'alertdialog');
        ov.setAttribute('aria-modal', 'true');
        ov.setAttribute('aria-label', 'QR Code lido');
        ov.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;align-items:center;' +
            'justify-content:center;gap:1.25rem;padding:1.5rem;text-align:center;color:#fff;background:#15803d;' +
            'animation:qr-fundo .25s ease-out';
        ov.innerHTML =
            '<div style="position:relative;width:9rem;height:9rem;display:flex;align-items:center;justify-content:center">' +
            '<span style="position:absolute;inset:0;border-radius:9999px;background:#fff;animation:qr-onda 1.2s ease-out .2s infinite"></span>' +
            '<div style="position:relative;width:9rem;height:9rem;border-radius:9999px;background:#fff;display:flex;align-items:center;justify-content:center;animation:qr-pulso .55s cubic-bezier(.2,.9,.3,1.2) both">' +
            '<svg viewBox="0 0 52 52" width="84" height="84" fill="none" stroke="#15803d" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M13 27l9 9 17-19" style="stroke-dasharray:50;stroke-dashoffset:50;animation:qr-traco .45s ease-out .4s forwards"/></svg></div></div>' +
            '<div style="animation:qr-texto .4s ease-out .5s both"><p style="margin:0;font-size:1.75rem;font-weight:700">QR Code lido!</p>' +
            '<p data-qr-codigo style="margin:.5rem 0 0;font-size:1.125rem;word-break:break-all;opacity:.95"></p></div>' +
            '<button type="button" style="animation:qr-texto .4s ease-out .7s both;min-height:3.5rem;min-width:min(80vw,16rem);' +
            'border:0;border-radius:.5rem;background:#fff;color:#15803d;font-size:1.25rem;font-weight:700;cursor:pointer">OK!</button>';
        ov.querySelector('[data-qr-codigo]').textContent = texto;
        var ok = ov.querySelector('button');
        ok.addEventListener('click', function () {
            ov.remove();
            if (campo.form) {
                if (campo.form.requestSubmit) campo.form.requestSubmit(); else campo.form.submit();
            }
        });
        document.body.appendChild(ov);
        ok.focus();
    }

    function lido(texto) {
        texto = (texto || '').trim();
        if (!texto) return false;
        alvo.value = texto;
        dlg.close();
        confirmar(texto);
        return true;
    }

    function decodificar() {
        if (detector) {
            return detector.detect(video).then(function (r) { return r.length ? r[0].rawValue : ''; });
        }
        if (!video.videoWidth) return Promise.resolve('');
        canvas.width = video.videoWidth; canvas.height = video.videoHeight;
        ctx.drawImage(video, 0, 0);
        var img = ctx.getImageData(0, 0, canvas.width, canvas.height);
        var r = window.jsQR && jsQR(img.data, img.width, img.height, { inversionAttempts: 'dontInvert' });
        return Promise.resolve(r ? r.data : '');
    }

    function ciclo() {
        if (!rodando) return;
        decodificar().then(function (texto) {
            if (rodando && !(texto && lido(texto))) setTimeout(function () { requestAnimationFrame(ciclo); }, 120);
        }).catch(function () { setTimeout(function () { requestAnimationFrame(ciclo); }, 250); });
    }

    function abrir(input) {
        alvo = input;
        msg.textContent = '';
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            alert('Este navegador não permite acessar a câmera (é preciso abrir o sistema por HTTPS).');
            return;
        }
        if (!detector && 'BarcodeDetector' in window) {
            try { detector = new BarcodeDetector({ formats: ['qr_code'] }); } catch (e) { detector = null; }
        }
        navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
            .then(function (s) {
                stream = s; video.srcObject = s;
                dlg.showModal();
                return video.play();
            })
            .then(function () { rodando = true; ciclo(); })
            .catch(function (e) {
                parar();
                if (dlg.open) dlg.close();
                alert(e && e.name === 'NotAllowedError'
                    ? 'Permissão da câmera negada. Libere o acesso à câmera nas configurações do navegador.'
                    : 'Não foi possível abrir a câmera.');
            });
    }

    botoes.forEach(function (b) {
        b.addEventListener('click', function () {
            var input = document.getElementById(b.dataset.leitorQr);
            if (input) abrir(input);
        });
    });
})();
