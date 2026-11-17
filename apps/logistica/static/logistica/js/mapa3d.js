// Mapa de Estoque 3D: corredor (A-T) no eixo X, guarda (01-21) no Z e andar (1-3) no Y.
// Bloco único de esteiras de fluxo (fileiras coladas): o palete entra pelo fundo e sai pela
// frente (guarda 01, do lado da câmera). Porta-paletes (montantes azuis, longarinas laranja),
// leito de roletes por andar, barra-guia azul separando cada corredor do vizinho, paletes de madeira e carga; botões para ocultar os andares de
// cima. Tudo em InstancedMesh (poucos draw calls) e render
// sob demanda (só redesenha ao interagir). Sombras só em desktop.
import * as THREE from 'three';
import { OrbitControls } from 'three-addons/OrbitControls.js';

const COR = {
    livre: 0x34d399,    // emerald-400
    ocupado: 0xe11d48,  // rose-600
    inativo: 0xcbd5e1,  // slate-300
};
const COR_DESTAQUE = 0xf59e0b; // amber-500
// Modo seleção (Romaneio): primeiro palete de cada corredor/andar e palete já escolhido.
// Cada produto (SKU) liberado para retirada recebe uma cor desta paleta (violeta e âmbar ficam reservados).
const CORES_PRODUTO = [
    0x2563eb, // blue-600
    0x16a34a, // green-600
    0xdc2626, // red-600
    0xea580c, // orange-600
    0x0d9488, // teal-600
    0xdb2777, // pink-600
    0x92400e, // amber-800
    0x0891b2, // cyan-600
    0x65a30d, // lime-600
    0x1e3a8a, // blue-900
];
const COR_MARCADO = 0x7c3aed;  // violet-600
const COR_INDISPONIVEL = 0xe2e8f0; // slate-200: posições que não podem ser escolhidas
const COR_FUNDO = 0xe2e8f0;
const COR_MONTANTE = 0x1d4ed8; // azul típico de porta-paletes
const COR_LONGARINA = 0xf97316; // laranja
const COR_FAIXA = 0xfacc15; // amarelo de demarcação
const COR_GUIA = COR_MONTANTE; // barra azul que separa um corredor do outro

// Medidas (unidades ≈ metros). Fileiras ao longo de Z, coladas lado a lado (bloco único).
const ESPACO = { x: 1.4, y: 1.9, z: 1.35 };
const ESTEIRA_Y = 0.3;          // altura dos roletes do andar 1
const LEITO_H = 0.08;           // espessura do leito de roletes
const GUARDAS_POR_VAO = 3;      // guardas entre dois pares de montantes
const PALETE = { x: 1.2, y: 0.14, z: 1.0 };
const CARGA = { x: 1.1, y: 1.05, z: 0.94 };
const LONGARINA_H = 0.12;
const GUIA = { w: 0.06, h: 0.18 }; // barra-guia entre corredores (acima dos roletes)
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

function detalheMarcado(e) {
    return { b: e.pl.b, sku: e.pl.sku, d: e.pl.d, l: e.pl.l, saldo: e.pl.saldo, c: e.c, r: e.r, a: e.a };
}

// ---------- Texturas geradas em canvas (nada de arquivos extras) ----------

function texturaCanvas(w, h, desenhar, srgb = true) {
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    desenhar(c.getContext('2d'), w, h);
    const t = new THREE.CanvasTexture(c);
    if (srgb) t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 4;
    return t;
}

// Concreto claro com manchas e juntas de dilatação nas bordas (repete sem emenda).
function texturaPiso() {
    const t = texturaCanvas(256, 256, (g, w, h) => {
        g.fillStyle = '#d6d3d1';
        g.fillRect(0, 0, w, h);
        for (let i = 0; i < 2600; i++) {
            const v = 150 + Math.floor(Math.random() * 80);
            g.fillStyle = `rgba(${v},${v},${v - 4},${Math.random() * 0.25})`;
            g.fillRect(Math.random() * w, Math.random() * h, 1 + Math.random() * 2, 1 + Math.random() * 2);
        }
        g.strokeStyle = 'rgba(120,113,108,0.18)';
        g.lineWidth = 1.5;
        g.strokeRect(1, 1, w - 2, h - 2);
    });
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    return t;
}

// Ripas de madeira do palete.
function texturaMadeira() {
    return texturaCanvas(128, 64, (g, w, h) => {
        g.fillStyle = '#c08a4e';
        g.fillRect(0, 0, w, h);
        const ripas = 5;
        for (let i = 0; i < ripas; i++) {
            const x = (i / ripas) * w;
            g.fillStyle = i % 2 ? '#b07a40' : '#c9955a';
            g.fillRect(x + 2, 0, w / ripas - 4, h);
            for (let k = 0; k < 6; k++) {
                g.strokeStyle = 'rgba(90,55,20,0.25)';
                g.beginPath();
                const y = Math.random() * h;
                g.moveTo(x + 3, y);
                g.lineTo(x + w / ripas - 3, y + (Math.random() - 0.5) * 6);
                g.stroke();
            }
        }
        g.fillStyle = 'rgba(60,35,10,0.55)';
        g.fillRect(0, h * 0.45, w, h * 0.12); // vão do garfo
    });
}

// Carga em caixas sob filme stretch: quase branco (a cor da instância multiplica), com
// emendas das caixas e reflexos do filme.
function texturaCarga() {
    return texturaCanvas(128, 128, (g, w, h) => {
        g.fillStyle = '#ffffff';
        g.fillRect(0, 0, w, h);
        g.strokeStyle = 'rgba(0,0,0,0.22)';
        g.lineWidth = 2;
        [[0, h / 2, w, h / 2], [w / 2, 0, w / 2, h / 2], [w / 3, h / 2, w / 3, h], [(2 * w) / 3, h / 2, (2 * w) / 3, h]]
            .forEach(([x1, y1, x2, y2]) => { g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke(); });
        g.strokeStyle = 'rgba(0,0,0,0.3)';
        g.strokeRect(1, 1, w - 2, h - 2);
        for (let i = 0; i < 6; i++) {
            g.fillStyle = `rgba(255,255,255,${0.12 + Math.random() * 0.15})`;
            g.fillRect(0, Math.random() * h, w, 2 + Math.random() * 4);
        }
        const brilho = g.createLinearGradient(0, 0, w, h);
        brilho.addColorStop(0, 'rgba(255,255,255,0.18)');
        brilho.addColorStop(0.5, 'rgba(255,255,255,0)');
        brilho.addColorStop(1, 'rgba(0,0,0,0.08)');
        g.fillStyle = brilho;
        g.fillRect(0, 0, w, h);
    });
}

// Roletes da esteira: faixas claras/escuras ao longo do comprimento (repetidas em Z).
function texturaRoletes() {
    const t = texturaCanvas(64, 64, (g, w, h) => {
        g.fillStyle = '#475569';
        g.fillRect(0, 0, w, h);
        const rolo = g.createLinearGradient(0, 8, 0, h - 8);
        rolo.addColorStop(0, '#94a3b8');
        rolo.addColorStop(0.45, '#f1f5f9');
        rolo.addColorStop(1, '#64748b');
        g.fillStyle = rolo;
        g.fillRect(0, 8, w, h - 16);
    });
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    return t;
}

function retanguloArredondado(g, x, y, w, h, r) {
    g.beginPath();
    g.moveTo(x + r, y);
    g.arcTo(x + w, y, x + w, y + h, r);
    g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r);
    g.arcTo(x, y, x + w, y, r);
    g.closePath();
}

// Placa de sinalização (sprite sempre virado para a câmera).
function rotuloSprite(texto, { fundo = null, cor = '#334155', escala = 1, largura = 1 } = {}) {
    const W = 128 * largura;
    const tex = texturaCanvas(W, 128, (g, w, h) => {
        if (fundo) {
            g.fillStyle = fundo;
            retanguloArredondado(g, 6, 10, w - 12, h - 20, 18);
            g.fill();
        }
        g.fillStyle = cor;
        g.font = `bold ${fundo ? 76 : 88}px sans-serif`;
        g.textAlign = 'center';
        g.textBaseline = 'middle';
        g.fillText(texto, w / 2, h / 2 + 4);
    });
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthWrite: false, fog: true }));
    s.scale.set(escala * largura, escala, 1);
    return s;
}

// Texto pintado no piso (largura x altura em metros).
function rotuloPiso(texto, largura, altura, cor = 'rgba(30,41,59,0.75)') {
    const W = Math.min(1024, Math.round(128 * largura / altura));
    const tex = texturaCanvas(W, 128, (g, w, h) => {
        g.fillStyle = cor;
        let fonte = 100;
        g.font = `bold ${fonte}px sans-serif`;
        while (fonte > 20 && g.measureText(texto).width > w * 0.92) {
            fonte -= 4;
            g.font = `bold ${fonte}px sans-serif`;
        }
        g.textAlign = 'center';
        g.textBaseline = 'middle';
        g.fillText(texto, w / 2, h / 2 + 6);
    });
    const m = new THREE.Mesh(
        new THREE.PlaneGeometry(largura, altura),
        new THREE.MeshLambertMaterial({ map: tex, transparent: true, depthWrite: false }),
    );
    m.rotation.x = -Math.PI / 2;
    m.receiveShadow = true;
    return m;
}

function instancias(geometria, material, matrizes, { sombra = false } = {}) {
    const m = new THREE.InstancedMesh(geometria, material, matrizes.length);
    matrizes.forEach((mx, i) => m.setMatrixAt(i, mx));
    m.instanceMatrix.needsUpdate = true;
    m.castShadow = sombra;
    m.receiveShadow = sombra;
    m.frustumCulled = false;
    return m;
}

async function iniciar() {
    if (!temWebGL()) return fallback('Este dispositivo não suporta WebGL.');

    let dados;
    try {
        // data-query fixa o filtro (ex.: Consultar Corredor); sem ele, vale a querystring da página.
        const filtro = raiz.dataset.query !== undefined ? raiz.dataset.query : window.location.search;
        const resp = await fetch(raiz.dataset.url + filtro, { credentials: 'same-origin' });
        if (!resp.ok) throw new Error(resp.status);
        dados = await resp.json();
    } catch (e) {
        return fallback('Não foi possível carregar os dados do mapa.');
    }
    const enderecos = dados.enderecos;
    if (!enderecos.length) return fallback('Nenhum endereço para exibir.');

    const movel = window.matchMedia('(pointer: coarse)').matches;
    const sombras = !movel;
    const renderer = new THREE.WebGLRenderer({ canvas: tela, antialias: !movel, powerPreference: movel ? 'low-power' : 'default' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, movel ? 1.5 : 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    if (sombras) {
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    }

    // Só as fileiras que têm endereços (ex.: Consultar Corredor mostra uma fileira só).
    // As fileiras são coladas lado a lado (bloco único); cada corredor/andar é uma esteira
    // de fluxo: entra pelo fundo e sai pela frente (guarda 01, do lado da câmera).
    const corredores = dados.corredores.split('').filter((c) => enderecos.some((e) => e.r === c));
    const nCorr = corredores.length;
    const nPos = dados.posicoes;
    const andares = dados.andares;
    const nAndares = andares.length;
    const larg = (nCorr - 1) * ESPACO.x;
    const prof = (nPos - 1) * ESPACO.z;
    const raioCena = Math.hypot(larg, prof) / 2 + 6;
    const xFileira = (i) => i * ESPACO.x - larg / 2;
    const xDivisa = (j) => xFileira(0) - ESPACO.x / 2 + j * ESPACO.x; // j = 0..nCorr
    const zGuarda = (p) => prof / 2 - (p - 1) * ESPACO.z;             // p = 1 (frente) .. nPos (fundo)
    const zFrente = prof / 2 + ESPACO.z / 2;
    const zFundo = -prof / 2 - ESPACO.z / 2;
    const yNivel = (a) => ESTEIRA_Y + (a - 1) * ESPACO.y;              // topo dos roletes do andar
    const alturaAte = (n) => yNivel(n) + PALETE.y + CARGA.y + 0.4;    // montante até o andar n
    const topo = alturaAte(nAndares);

    const cena = new THREE.Scene();
    cena.background = new THREE.Color(COR_FUNDO);
    cena.fog = new THREE.Fog(COR_FUNDO, raioCena * 2.2, raioCena * 5);

    cena.add(new THREE.HemisphereLight(0xffffff, 0x9ca3af, 1.9));
    const sol = new THREE.DirectionalLight(0xffffff, 2.1);
    sol.position.set(-raioCena * 0.6, raioCena * 1.3, raioCena * 0.8);
    if (sombras) {
        sol.castShadow = true;
        sol.shadow.mapSize.set(2048, 2048);
        Object.assign(sol.shadow.camera, {
            left: -raioCena, right: raioCena, top: raioCena, bottom: -raioCena, near: 1, far: raioCena * 4,
        });
        sol.shadow.bias = -0.0005;
        sol.shadow.normalBias = 0.03;
    }
    cena.add(sol);

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 800);
    const controles = new OrbitControls(camera, tela);
    controles.enableDamping = false;
    controles.maxPolarAngle = Math.PI / 2 - 0.02;
    controles.screenSpacePanning = true;

    // ---------- Piso ----------
    const tamPiso = { x: larg + ESPACO.x + 14, z: prof + 18 };
    const texPiso = texturaPiso();
    texPiso.repeat.set(tamPiso.x / 4, tamPiso.z / 4);
    const piso = new THREE.Mesh(
        new THREE.PlaneGeometry(tamPiso.x, tamPiso.z),
        new THREE.MeshStandardMaterial({ map: texPiso, roughness: 0.92 }),
    );
    piso.rotation.x = -Math.PI / 2;
    piso.receiveShadow = true;
    cena.add(piso);

    // Faixa amarela em volta do bloco.
    const m4 = new THREE.Matrix4();
    const Q0 = new THREE.Quaternion();
    const folga = 0.35;
    const bx0 = xDivisa(0) - folga;
    const bx1 = xDivisa(nCorr) + folga;
    const bz0 = zFundo - folga;
    const bz1 = zFrente + folga;
    cena.add(instancias(
        new THREE.BoxGeometry(1, 0.004, 1),
        new THREE.MeshStandardMaterial({ color: COR_FAIXA, roughness: 0.7 }),
        [
            [(bx0 + bx1) / 2, bz0, bx1 - bx0 + 0.1, 0.1], [(bx0 + bx1) / 2, bz1, bx1 - bx0 + 0.1, 0.1],
            [bx0, (bz0 + bz1) / 2, 0.1, bz1 - bz0], [bx1, (bz0 + bz1) / 2, 0.1, bz1 - bz0],
        ].map(([x, z, sx, sz]) => new THREE.Matrix4().compose(new THREE.Vector3(x, 0.004, z), Q0, new THREE.Vector3(sx, 1, sz))),
    ));

    // ---------- Porta-paletes com esteiras ----------
    // Peças por andar ficam em `porAndar` para o corte ("ocultar andares de cima").
    const porAndar = []; // { malha, i, matriz, andar }
    const ZERO = new THREE.Matrix4().makeScale(0, 0, 0);
    function porAndarMalha(geometria, material, itens, sombra) {
        const malhaAndar = instancias(geometria, material, itens.map((x) => x.matriz), { sombra });
        itens.forEach((x, i) => porAndar.push({ malha: malhaAndar, i, matriz: x.matriz, andar: x.andar }));
        cena.add(malhaAndar);
        return malhaAndar;
    }
    const nVaos = Math.ceil(nPos / GUARDAS_POR_VAO);
    const zVao = (k) => Math.max(zFrente - k * GUARDAS_POR_VAO * ESPACO.z, zFundo); // k = 0..nVaos
    const metal = (cor) => new THREE.MeshStandardMaterial({ color: cor, roughness: 0.55, metalness: 0.25 });

    // Montantes nas divisas entre fileiras (compartilhados), em cada divisa de vão.
    const geoMontante = new THREE.BoxGeometry(0.09, 1, 0.09).translate(0, 0.5, 0);
    const posMontantes = [];
    for (let j = 0; j <= nCorr; j++) {
        for (let k = 0; k <= nVaos; k++) posMontantes.push([xDivisa(j), zVao(k)]);
    }
    const malhaMontantes = instancias(geoMontante, metal(COR_MONTANTE), posMontantes.map(() => new THREE.Matrix4()), { sombra: sombras });
    cena.add(malhaMontantes);
    function alturaMontantes(h) {
        posMontantes.forEach(([x, z], i) => malhaMontantes.setMatrixAt(
            i, m4.compose(new THREE.Vector3(x, 0, z), Q0, new THREE.Vector3(1, h, 1))));
        malhaMontantes.instanceMatrix.needsUpdate = true;
    }
    alturaMontantes(topo);

    // Longarinas (uma por divisa, vão e andar) e leito de roletes (um por fileira e andar).
    const longarinas = [];
    const leitos = [];
    const guias = []; // barras-guia: uma por divisa e andar, da entrada à saída
    andares.forEach((a) => {
        const yLong = yNivel(a) - LEITO_H - LONGARINA_H / 2;
        for (let j = 0; j <= nCorr; j++) {
            guias.push({
                andar: a,
                matriz: new THREE.Matrix4().makeTranslation(xDivisa(j), yNivel(a) + GUIA.h / 2, (zFrente + zFundo) / 2),
            });
            for (let k = 0; k < nVaos; k++) {
                longarinas.push({
                    andar: a,
                    matriz: new THREE.Matrix4().compose(
                        new THREE.Vector3(xDivisa(j), yLong, (zVao(k) + zVao(k + 1)) / 2), Q0,
                        new THREE.Vector3(1, 1, zVao(k) - zVao(k + 1))),
                });
            }
        }
        corredores.forEach((_, i) => leitos.push({
            andar: a,
            matriz: new THREE.Matrix4().makeTranslation(xFileira(i), yNivel(a) - LEITO_H / 2, (zFrente + zFundo) / 2),
        }));
    });
    porAndarMalha(new THREE.BoxGeometry(0.07, LONGARINA_H, 1), metal(COR_LONGARINA), longarinas, sombras);
    const texRoletes = texturaRoletes();
    texRoletes.repeat.set(1, (zFrente - zFundo) / 0.22);
    porAndarMalha(
        new THREE.BoxGeometry(ESPACO.x - 0.12, LEITO_H, zFrente - zFundo),
        new THREE.MeshStandardMaterial({ map: texRoletes, roughness: 0.4, metalness: 0.35 }),
        leitos, sombras,
    );
    porAndarMalha(new THREE.BoxGeometry(GUIA.w, GUIA.h, zFrente - zFundo), metal(COR_GUIA), guias, sombras);
    // Barras atravessadas separando cada guarda da seguinte (e fechando frente e fundo):
    // com as barras-guia, formam uma grade azul em volta de cada posição.
    const travessas = [];
    andares.forEach((a) => {
        for (let p = 0; p <= nPos; p++) {
            travessas.push({
                andar: a,
                matriz: new THREE.Matrix4().makeTranslation(0, yNivel(a) + GUIA.h / 2, zFrente - p * ESPACO.z),
            });
        }
    });
    porAndarMalha(
        new THREE.BoxGeometry(xDivisa(nCorr) - xDivisa(0), GUIA.h, GUIA.w),
        metal(COR_GUIA), travessas, sombras,
    );

    // Travamento em zigue-zague nas laterais de cada corredor (todas as divisas), um por vão:
    // separa visualmente um corredor do vizinho.
    const travas = [];
    Array.from({ length: nCorr + 1 }, (_, j) => xDivisa(j)).forEach((x) => {
        for (let k = 0; k < nVaos; k++) {
            const zs = [zVao(k), zVao(k + 1)];
            const passo = 0.7;
            for (let y = 0.15, l = 0; y + passo <= topo; y += passo, l = 1 - l) {
                travas.push(x, y, zs[l], x, y + passo, zs[1 - l]);
            }
        }
    });
    const malhaTravas = new THREE.LineSegments(
        new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(travas, 3)),
        new THREE.LineBasicMaterial({ color: 0x3b82f6 }),
    );
    cena.add(malhaTravas);

    // ---------- Sinalização ----------
    const placas = [];
    corredores.forEach((c, i) => {
        [zFrente + 0.3, zFundo - 0.3].forEach((z) => {
            const placa = rotuloSprite(c, { fundo: '#1e293b', cor: '#ffffff', escala: 0.95 });
            placa.position.set(xFileira(i), topo + 0.6, z);
            placas.push(placa);
            cena.add(placa);
        });
        const letra = rotuloPiso(c, 1.2, 1.2);
        letra.position.set(xFileira(i), 0.006, zFrente + 1.2);
        cena.add(letra);
    });
    const largTexto = Math.max(larg + ESPACO.x, 6);
    const saida = rotuloPiso('▼ SAÍDA', Math.min(largTexto, 12) * 0.6, 0.8, '#15803d');
    saida.position.set(0, 0.006, zFrente + 2.3);
    cena.add(saida);
    const entrada = rotuloPiso('ENTRADA ▼', Math.min(largTexto, 12) * 0.6, 0.7, '#475569');
    entrada.position.set(0, 0.006, zFundo - 1.4);
    cena.add(entrada);
    // Andares na frente, à esquerda do bloco; guardas ao longo da lateral direita.
    const rotulosAndar = andares.map((a) => {
        const s = rotuloSprite(String(a), { fundo: '#f59e0b', cor: '#1e293b', escala: 0.55 });
        s.position.set(xDivisa(0) - 0.45, yNivel(a) + 0.55, zFrente);
        s.userData.andar = a;
        cena.add(s);
        return s;
    });
    for (let p = 1; p <= nPos; p++) {
        const s = rotuloSprite(String(p).padStart(2, '0'), { fundo: '#ffffff', cor: '#334155', escala: 0.7, largura: 1.3 });
        s.position.set(xDivisa(nCorr) + 0.6, 0.35, zGuarda(p));
        cena.add(s);
    }

    // Modo seleção (data-selecao): só o primeiro palete de cada corredor/andar (`pl.f`) é clicável;
    // os clicados ficam em `marcados` (barcode -> dados) e a página é avisada por eventos.
    const selecao = raiz.dataset.selecao !== undefined;
    const marcados = new Map();
    // SKU -> { cor, descricao, qtd } dos paletes liberados, em ordem de SKU (cores estáveis entre aberturas).
    const produtos = new Map();
    if (selecao) {
        enderecos.filter((e) => e.pl && e.pl.f)
            .sort((x, y) => x.pl.sku.localeCompare(y.pl.sku))
            .forEach((e) => {
                if (!produtos.has(e.pl.sku)) {
                    produtos.set(e.pl.sku, { cor: CORES_PRODUTO[produtos.size % CORES_PRODUTO.length], descricao: e.pl.d, qtd: 0 });
                }
                produtos.get(e.pl.sku).qtd += 1;
            });
    }
    const corBase = (i) => {
        const e = enderecos[i];
        if (!selecao) return COR[e.s];
        if (e.pl && marcados.has(e.pl.b)) return COR_MARCADO;
        if (e.pl && e.pl.f) return produtos.get(e.pl.sku).cor;
        return COR_INDISPONIVEL; // só os primeiros de cada corredor/andar se destacam
    };
    const legenda = document.getElementById('mapa3d-legenda');
    if (selecao && legenda) {
        const item = (hex, texto) => `<span class="inline-flex items-center gap-1.5"><span class="inline-block h-4 w-4 shrink-0 rounded border border-slate-300" style="background:#${hex.toString(16).padStart(6, '0')}"></span>${texto}</span>`;
        legenda.innerHTML = [...produtos].map(([sku, p]) => item(p.cor, `${esc(p.descricao)} <span class="text-slate-500">(${esc(sku)}) · ${p.qtd}</span>`)).join('')
            + item(COR_MARCADO, 'Selecionado') + item(COR_INDISPONIVEL, 'Indisponível');
        legenda.hidden = false;
    }

    // ---------- Paletes e carga ----------
    // Uma instância por endereço (o índice da instância = índice em `enderecos`, usado no raycast).
    // Ocupado: caixa de carga sobre palete de madeira. Livre/inativo: placa fina sobre o nível.
    const temCarga = (e) => !!e.pl || e.s === 'ocupado';
    const posicaoDe = (e) => new THREE.Vector3(
        xFileira(corredores.indexOf(e.r)),
        yNivel(e.a) + (temCarga(e) ? PALETE.y + CARGA.y / 2 : 0.02),
        zGuarda(e.p),
    );
    const escalaDe = (e) => (temCarga(e)
        ? new THREE.Vector3(CARGA.x, CARGA.y, CARGA.z)
        : new THREE.Vector3(ESPACO.x - 0.25, 0.04, ESPACO.z - 0.25));
    const matrizDe = (e) => m4.compose(posicaoDe(e), new THREE.Quaternion(), escalaDe(e));

    const malha = new THREE.InstancedMesh(
        new THREE.BoxGeometry(1, 1, 1),
        new THREE.MeshStandardMaterial({ map: texturaCarga(), roughness: 0.6 }),
        enderecos.length,
    );
    malha.castShadow = sombras;
    malha.receiveShadow = sombras;
    malha.frustumCulled = false;
    const cor = new THREE.Color();
    // Paletes já escolhidos antes de um erro de validação (a página reenvia os códigos).
    (raiz.dataset.marcados || '').split(',').filter(Boolean).forEach((b) => {
        const e = enderecos.find((x) => x.pl && x.pl.b === b);
        if (e && selecao) marcados.set(b, detalheMarcado(e));
    });
    const bases = [];
    enderecos.forEach((e, i) => {
        const matriz = matrizDe(e).clone();
        malha.setMatrixAt(i, matriz);
        malha.setColorAt(i, cor.set(corBase(i)));
        porAndar.push({ malha, i, matriz, andar: e.a });
        if (temCarga(e)) {
            const p = posicaoDe(e);
            bases.push({ andar: e.a, matriz: new THREE.Matrix4().makeTranslation(p.x, yNivel(e.a) + PALETE.y / 2, p.z) });
        }
    });
    malha.instanceMatrix.needsUpdate = true;
    malha.instanceColor.needsUpdate = true;
    malha.computeBoundingSphere();
    cena.add(malha);
    if (bases.length) {
        porAndarMalha(
            new THREE.BoxGeometry(PALETE.x, PALETE.y, PALETE.z),
            new THREE.MeshStandardMaterial({ map: texturaMadeira(), roughness: 0.85 }),
            bases, sombras,
        );
    }

    // Corte por andar: esconde os andares acima de `nivelMax` (instâncias com escala 0,
    // montantes encurtados) para enxergar e clicar nos de baixo.
    let nivelMax = nAndares;
    function aplicarCorte(n) {
        nivelMax = n;
        const tocadas = new Set();
        porAndar.forEach((x) => {
            x.malha.setMatrixAt(x.i, x.andar > n ? ZERO : x.matriz);
            tocadas.add(x.malha);
        });
        tocadas.forEach((m) => { m.instanceMatrix.needsUpdate = true; });
        const h = alturaAte(n);
        alturaMontantes(h);
        malhaTravas.visible = n === nAndares;
        placas.forEach((pl) => { pl.position.y = h + 0.6; });
        rotulosAndar.forEach((r) => { r.visible = r.userData.andar <= n; });
        if (selecionado >= 0 && enderecos[selecionado].a > n) mostrarDetalhe(-1);
        pedirQuadro();
    }

    // Contorno da posição selecionada.
    const contorno = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
        new THREE.LineBasicMaterial({ color: COR_DESTAQUE }),
    );
    contorno.matrixAutoUpdate = false;
    contorno.visible = false;
    cena.add(contorno);

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
    const alvoCentro = new THREE.Vector3(0, topo / 3, 1.5); // um pouco à frente: inclui a faixa de SAÍDA
    // Distância para caber `largura` x `altura` (vistas pela câmera) na tela, mais a profundidade.
    const distanciaPara = (largura, altura, fundo) => {
        const vfov = THREE.MathUtils.degToRad(camera.fov) / 2;
        const hfov = Math.atan(Math.tan(vfov) * camera.aspect);
        return Math.max(largura / 2 / Math.tan(hfov), altura / 2 / Math.tan(vfov)) + fundo / 2;
    };
    const deDirecao = (dx, dy, dz, dist) => new THREE.Vector3(dx, dy, dz).normalize().multiplyScalar(dist).add(alvoCentro);
    const W = larg + ESPACO.x + 4;
    const D = prof + 9;
    const deFrente = W >= D * 0.6; // bloco largo: olha pela saída; poucas fileiras: de lado
    const presets = {
        // Fileira única (ou poucas): olha de lado para o comprimento ocupar a largura da tela.
        iso: () => (deFrente
            ? [deDirecao(0.3, 0.75, 1, distanciaPara(W, D * 0.7 + topo, D)), alvoCentro]
            : [deDirecao(1, 0.75, 0.45, distanciaPara(D, W * 0.7 + topo, W) * 1.15), alvoCentro]),
        topo: () => [new THREE.Vector3(0, distanciaPara(W, D, 0) + topo, 0.01), new THREE.Vector3(0, 0, 0)],
        frontal: () => [deDirecao(0, 0.25, 1, distanciaPara(W, topo + 2, D)), alvoCentro],
        lateral: () => [deDirecao(1, 0.25, 0, distanciaPara(D, topo + 2, W)), alvoCentro],
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
        destacados.forEach((i) => pintar(i, corBase(i)));
        destacados = [];
    }
    function alvoEm(ev) {
        const r = tela.getBoundingClientRect();
        ponteiro.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
        raio.setFromCamera(ponteiro, camera);
        const hit = raio.intersectObject(malha).find((h) => enderecos[h.instanceId].a <= nivelMax);
        return hit ? hit.instanceId : -1;
    }
    function descricao(e) {
        const linhas = [`<strong>${e.c}</strong>`, ROTULO_ESTADO[e.s]];
        if (e.pl) linhas.push(`${esc(e.pl.sku)} · ${esc(e.pl.d)}`, `Lote ${esc(e.pl.l)}`);
        if (selecao && e.pl) {
            linhas.push(e.pl.f
                ? `Saldo ${esc(e.pl.saldo)} kg · desde ${esc(e.pl.desde)}<br><strong>Clique para selecionar</strong>`
                : 'Não é o primeiro deste corredor/andar');
        }
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
        tela.style.cursor = i >= 0 ? 'pointer' : '';
        if (i < 0) { dica.hidden = true; return; }
        dica.innerHTML = descricao(enderecos[i]);
        const r = raiz.getBoundingClientRect();
        dica.style.left = `${ev.clientX - r.left + 14}px`;
        dica.style.top = `${ev.clientY - r.top + 14}px`;
        dica.hidden = false;
    });
    tela.addEventListener('pointerleave', () => { dica.hidden = true; });

    // Modo seleção: clicar num primeiro palete marca/desmarca; a página monta a lista de retirada.
    function avisarSelecao() {
        window.dispatchEvent(new CustomEvent('mapa3d:selecao', { detail: { marcados: [...marcados.values()] } }));
        pedirQuadro();
    }
    function alternarMarca(i) {
        const e = i >= 0 ? enderecos[i] : null;
        if (!e || !e.pl) return;
        if (!e.pl.f) {
            window.dispatchEvent(new CustomEvent('mapa3d:invalido', { detail: { c: e.c, b: e.pl.b } }));
            return;
        }
        if (marcados.has(e.pl.b)) marcados.delete(e.pl.b);
        else marcados.set(e.pl.b, detalheMarcado(e));
        pintar(i, corBase(i));
        avisarSelecao();
    }
    window.addEventListener('mapa3d:remover', (ev) => {
        if (!marcados.delete(ev.detail.b)) return;
        const i = enderecos.findIndex((x) => x.pl && x.pl.b === ev.detail.b);
        if (i >= 0) pintar(i, corBase(i));
        avisarSelecao();
    });

    function contornar(i) {
        contorno.visible = i >= 0;
        if (i < 0) return;
        const e = enderecos[i];
        const esc3 = escalaDe(e).addScalar(0.08);
        contorno.matrix.compose(posicaoDe(e), new THREE.Quaternion(), esc3);
    }

    function mostrarDetalhe(i) {
        if (selecionado >= 0 && !destacados.includes(selecionado)) pintar(selecionado, corBase(selecionado));
        selecionado = i;
        contornar(i);
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
        if (selecao) alternarMarca(alvoEm(ev));
        else mostrarDetalhe(alvoEm(ev));
    });
    painel.querySelector('[data-fechar]').addEventListener('click', () => mostrarDetalhe(-1));

    // Busca por código, SKU ou barcode do palete (caixa de busca ou ?destacar=).
    function buscar(texto) {
        const q = texto.trim().toUpperCase();
        limparDestaques();
        if (msgBusca) msgBusca.textContent = '';
        if (!q) { pedirQuadro(); return; }
        enderecos.forEach((e, i) => {
            if (e.c.includes(q) || (e.pl && (e.pl.sku.toUpperCase().includes(q) || e.pl.b.toUpperCase().includes(q)))) {
                destacados.push(i);
            }
        });
        destacados.forEach((i) => pintar(i, COR_DESTAQUE));
        if (destacados.some((i) => enderecos[i].a > nivelMax)) aplicarCorte(nAndares);
        if (msgBusca) {
            msgBusca.textContent = destacados.length
                ? `${destacados.length} encontrado(s)` : 'Nada encontrado';
        }
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
    // A caixa de busca só existe em Consultar Corredor.
    if (busca) {
        document.getElementById('mapa3d-busca-form').addEventListener('submit', (ev) => { ev.preventDefault(); buscar(busca.value); });
        busca.addEventListener('input', () => { if (!busca.value) buscar(''); });
    }

    carregando.hidden = true;
    ajustar();
    irPara('iso'); // depois de ajustar: o enquadramento depende da proporção da tela
    if (selecao && marcados.size) avisarSelecao();
    if (!selecao && raiz.dataset.destacar) {
        if (busca) busca.value = raiz.dataset.destacar;
        buscar(raiz.dataset.destacar);
    }
}

iniciar().catch(() => fallback('Erro ao iniciar o mapa 3D.'));
