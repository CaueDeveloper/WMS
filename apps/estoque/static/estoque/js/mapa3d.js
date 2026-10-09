// Mapa de Estoque 3D: corredor (A-T) no eixo X, guarda (01-21) no Z e andar (1-3) no Y.
// Uma única InstancedMesh (1 draw call) e render sob demanda (só redesenha ao interagir).
import * as THREE from 'three';
import { OrbitControls } from 'three-addons/OrbitControls.js';

const COR = {
    livre: 0x34d399,    // emerald-400
    ocupado: 0xe11d48,  // rose-600
    inativo: 0xcbd5e1,  // slate-300
};
const COR_DESTAQUE = 0xf59e0b; // amber-500
const ESPACO = { x: 2.2, y: 2.0, z: 1.3 };
const CAIXA = { x: 1.3, y: 0.7, z: 0.9 };
const ROTULO_ESTADO = { livre: 'Livre', ocupado: 'Ocupado', inativo: 'Inativo' };

const raiz = document.getElementById('mapa3d');
const tela = document.getElementById('mapa3d-canvas');
const carregando = document.getElementById('mapa3d-carregando');
const dica = document.getElementById('mapa3d-dica');
const painel = document.getElementById('mapa3d-detalhe');
const busca = document.getElementById('mapa3d-busca');
const msgBusca = document.getElementById('mapa3d-busca-msg');

function fallback(motivo) {
    carregando.hidden = true;
    const aviso = document.getElementById('mapa3d-fallback');
    aviso.querySelector('[data-motivo]').textContent = motivo;
    aviso.hidden = false;
}

function temWebGL() {
    try {
        const c = document.createElement('canvas');
        return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
    } catch (e) {
        return false;
    }
}

function rotuloSprite(texto) {
    const c = document.createElement('canvas');
    c.width = c.height = 64;
    const g = c.getContext('2d');
    g.fillStyle = '#334155';
    g.font = 'bold 44px sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(texto, 32, 34);
    const tex = new THREE.CanvasTexture(c);
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthWrite: false }));
    s.scale.set(1, 1, 1);
    return s;
}

async function iniciar() {
    if (!temWebGL()) return fallback('Este dispositivo não suporta WebGL.');

    let dados;
    try {
        const resp = await fetch(raiz.dataset.url + window.location.search, { credentials: 'same-origin' });
        if (!resp.ok) throw new Error(resp.status);
        dados = await resp.json();
    } catch (e) {
        return fallback('Não foi possível carregar os dados do mapa.');
    }
    const enderecos = dados.enderecos;
    if (!enderecos.length) return fallback('Nenhum endereço para exibir.');

    const movel = window.matchMedia('(pointer: coarse)').matches;
    const renderer = new THREE.WebGLRenderer({ canvas: tela, antialias: !movel, powerPreference: 'low-power' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, movel ? 1.5 : 2));

    const cena = new THREE.Scene();
    cena.background = new THREE.Color(0xf1f5f9);
    cena.add(new THREE.AmbientLight(0xffffff, 0.75));
    const sol = new THREE.DirectionalLight(0xffffff, 0.9);
    sol.position.set(-20, 40, 25);
    cena.add(sol);

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 500);
    const controles = new OrbitControls(camera, tela);
    controles.enableDamping = false;
    controles.maxPolarAngle = Math.PI / 2 - 0.02;
    controles.screenSpacePanning = true;

    const corredores = dados.corredores.split('');
    const nCorr = corredores.length;
    const nPos = dados.posicoes;
    const andares = dados.andares;
    const larg = (nCorr - 1) * ESPACO.x;
    const prof = (nPos - 1) * ESPACO.z;
    const posicaoDe = (e) => new THREE.Vector3(
        corredores.indexOf(e.r) * ESPACO.x - larg / 2,
        (e.a - 1) * ESPACO.y + CAIXA.y / 2,
        (e.p - 1) * ESPACO.z - prof / 2,
    );

    // Piso e rótulos dos corredores/andares.
    const piso = new THREE.GridHelper(Math.max(larg, prof) + 6, Math.round((Math.max(larg, prof) + 6) / 1.25), 0x94a3b8, 0xcbd5e1);
    piso.position.y = -0.02;
    cena.add(piso);
    corredores.forEach((c, i) => {
        const s = rotuloSprite(c);
        s.position.set(i * ESPACO.x - larg / 2, 0.15, -prof / 2 - 1.6);
        cena.add(s);
    });
    // Montantes verticais (estrutura do porta-paletes) em um único LineSegments.
    const pts = [];
    const topo = (andares.length - 1) * ESPACO.y + CAIXA.y + 0.1;
    corredores.forEach((_, i) => {
        for (let p = 0; p < nPos; p += 4) {
            const x = i * ESPACO.x - larg / 2;
            const z = p * ESPACO.z - prof / 2;
            pts.push(x, 0, z, x, topo, z);
        }
    });
    const montantes = new THREE.LineSegments(
        new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(pts, 3)),
        new THREE.LineBasicMaterial({ color: 0x94a3b8 }),
    );
    cena.add(montantes);

    // Instâncias.
    const malha = new THREE.InstancedMesh(
        new THREE.BoxGeometry(CAIXA.x, CAIXA.y, CAIXA.z),
        new THREE.MeshLambertMaterial(),
        enderecos.length,
    );
    const m4 = new THREE.Matrix4();
    const cor = new THREE.Color();
    enderecos.forEach((e, i) => {
        m4.makeTranslation(posicaoDe(e));
        malha.setMatrixAt(i, m4);
        malha.setColorAt(i, cor.set(COR[e.s]));
    });
    malha.instanceMatrix.needsUpdate = true;
    malha.instanceColor.needsUpdate = true;
    cena.add(malha);

    let pendente = false;
    const desenhar = () => {
        pendente = false;
        renderer.render(cena, camera);
    };
    const pedirQuadro = () => {
        if (!pendente) {
            pendente = true;
            requestAnimationFrame(desenhar);
        }
    };
    controles.addEventListener('change', pedirQuadro);

    function ajustar() {
        const w = tela.parentElement.clientWidth;
        const h = tela.parentElement.clientHeight;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        pedirQuadro();
    }
    new ResizeObserver(ajustar).observe(tela.parentElement);

    // Presets de câmera.
    const alvoCentro = new THREE.Vector3(0, ((andares.length - 1) * ESPACO.y) / 2, 0);
    const presets = {
        iso: () => [new THREE.Vector3(larg * 0.7, 30, prof * 1.1 + 14), alvoCentro],
        topo: () => [new THREE.Vector3(0, Math.max(larg, prof) * 1.1 + 6, 0.01), new THREE.Vector3(0, 0, 0)],
        frontal: () => [new THREE.Vector3(0, 8, prof / 2 + 40), alvoCentro],
        lateral: () => [new THREE.Vector3(larg / 2 + 40, 8, 0), alvoCentro],
    };
    function irPara(nome) {
        const [pos, alvo] = presets[nome]();
        camera.position.copy(pos);
        controles.target.copy(alvo);
        controles.update();
        pedirQuadro();
    }
    document.querySelectorAll('[data-camera]').forEach((b) =>
        b.addEventListener('click', () => irPara(b.dataset.camera)));
    irPara('iso');

    // Seleção/hover por raycast.
    const raio = new THREE.Raycaster();
    const ponteiro = new THREE.Vector2();
    let selecionado = -1;
    let destacados = [];

    function pintar(i, hex) {
        malha.setColorAt(i, cor.set(hex));
        malha.instanceColor.needsUpdate = true;
    }
    function limparDestaques() {
        destacados.forEach((i) => pintar(i, COR[enderecos[i].s]));
        destacados = [];
    }
    function alvoEm(ev) {
        const r = tela.getBoundingClientRect();
        ponteiro.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
        raio.setFromCamera(ponteiro, camera);
        const hit = raio.intersectObject(malha)[0];
        return hit ? hit.instanceId : -1;
    }
    function descricao(e) {
        const linhas = [`<strong>${e.c}</strong>`, ROTULO_ESTADO[e.s]];
        if (e.pl) linhas.push(`${esc(e.pl.sku)} · ${esc(e.pl.d)}`, `Lote ${esc(e.pl.l)}`);
        return linhas.join('<br>');
    }
    function esc(t) {
        const d = document.createElement('div');
        d.textContent = t == null ? '' : String(t);
        return d.innerHTML;
    }

    tela.addEventListener('pointermove', (ev) => {
        if (ev.pointerType !== 'mouse' || ev.buttons) { dica.hidden = true; return; }
        const i = alvoEm(ev);
        if (i < 0) { dica.hidden = true; return; }
        dica.innerHTML = descricao(enderecos[i]);
        const r = raiz.getBoundingClientRect();
        dica.style.left = `${ev.clientX - r.left + 14}px`;
        dica.style.top = `${ev.clientY - r.top + 14}px`;
        dica.hidden = false;
    });
    tela.addEventListener('pointerleave', () => { dica.hidden = true; });

    function mostrarDetalhe(i) {
        if (selecionado >= 0 && !destacados.includes(selecionado)) pintar(selecionado, COR[enderecos[selecionado].s]);
        selecionado = i;
        if (i < 0) { painel.hidden = true; pedirQuadro(); return; }
        const e = enderecos[i];
        pintar(i, COR_DESTAQUE);
        let html = `<p class="text-sm font-semibold text-slate-900">${e.c}</p>
            <p class="text-xs text-slate-600">Corredor ${e.r} · Andar ${e.a} · Guarda ${String(e.p).padStart(2, '0')}</p>
            <p class="mt-2 text-sm text-slate-800">${ROTULO_ESTADO[e.s]}</p>`;
        if (e.pl) {
            html += `<dl class="mt-2 space-y-0.5 text-sm text-slate-700">
                <div><dt class="inline text-slate-500">Palete: </dt><dd class="inline">${esc(e.pl.b)}</dd></div>
                <div><dt class="inline text-slate-500">SKU: </dt><dd class="inline">${esc(e.pl.sku)}</dd></div>
                <div><dt class="inline text-slate-500">Item: </dt><dd class="inline">${esc(e.pl.d)}</dd></div>
                <div><dt class="inline text-slate-500">Lote: </dt><dd class="inline">${esc(e.pl.l)}</dd></div>
            </dl>
            <a class="btn mt-3 inline-flex min-h-10 items-center" href="${esc(e.pl.url)}">Inspecionar palete</a>`;
        }
        painel.querySelector('[data-conteudo]').innerHTML = html;
        painel.hidden = false;
        pedirQuadro();
    }
    let inicio = null;
    tela.addEventListener('pointerdown', (ev) => { inicio = [ev.clientX, ev.clientY]; });
    tela.addEventListener('pointerup', (ev) => {
        if (!inicio || Math.hypot(ev.clientX - inicio[0], ev.clientY - inicio[1]) > 6) return; // foi arrasto
        mostrarDetalhe(alvoEm(ev));
    });
    painel.querySelector('[data-fechar]').addEventListener('click', () => mostrarDetalhe(-1));

    // Busca por código, SKU ou barcode do palete.
    function buscar() {
        const q = busca.value.trim().toUpperCase();
        limparDestaques();
        msgBusca.textContent = '';
        if (!q) { pedirQuadro(); return; }
        enderecos.forEach((e, i) => {
            if (e.c.includes(q) || (e.pl && (e.pl.sku.toUpperCase().includes(q) || e.pl.b.toUpperCase().includes(q)))) {
                destacados.push(i);
            }
        });
        destacados.forEach((i) => pintar(i, COR_DESTAQUE));
        msgBusca.textContent = destacados.length
            ? `${destacados.length} encontrado(s)` : 'Nada encontrado';
        if (destacados.length) {
            const centro = new THREE.Vector3();
            destacados.forEach((i) => centro.add(posicaoDe(enderecos[i])));
            centro.divideScalar(destacados.length);
            const raioBusca = destacados.reduce((m, i) => Math.max(m, posicaoDe(enderecos[i]).distanceTo(centro)), 0);
            const dist = Math.min(Math.max(9, raioBusca * 1.6), 70);
            controles.target.copy(centro);
            camera.position.copy(centro).add(new THREE.Vector3(dist * 0.6, dist * 0.5, dist));
            controles.update();
        }
        pedirQuadro();
    }
    document.getElementById('mapa3d-busca-form').addEventListener('submit', (ev) => { ev.preventDefault(); buscar(); });
    busca.addEventListener('input', () => { if (!busca.value) buscar(); });

    carregando.hidden = true;
    ajustar();
}

iniciar().catch(() => fallback('Erro ao iniciar o mapa 3D.'));
