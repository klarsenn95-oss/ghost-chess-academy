/* Ghost Puzzle Board — plateau d'échecs interactif minimal, façon Lichess.
 * Dépend de chess.js (window.Chess) pour la légalité des coups.
 * Usage :
 *   const board = createPuzzleBoard(containerEl, {
 *     fen: 'start' ou une position FEN,
 *     orientation: 'white' | 'black',
 *     interactive: true,
 *     onAttemptMove: (from, to, promotion) => {...}  // appelé quand le joueur tente un coup légal
 *   });
 *   board.setFen(newFen);
 *   board.flashSquare('e4', 'good'|'bad');
 *   board.showArrow('d1','d5');
 *   board.clearArrows();
 *   board.destroy();
 */
(function (global) {
  // Jeu de pièces "cburnett" (Colin M. L. Burnett, CC BY-SA 3.0 / GFDL —
  // le même jeu utilisé par défaut sur Lichess), self-hosté sous
  // static/pieces/cburnett/ plutôt que des glyphes Unicode dont le rendu
  // varie trop d'une police/d'un OS à l'autre pour ressembler à un vrai
  // plateau.
  const FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
  const PIECE_TYPE_LETTER = { p: 'P', n: 'N', b: 'B', r: 'R', q: 'Q', k: 'K' };
  const DEFAULT_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

  function pieceImageUrl(piece) {
    if (!piece) return null;
    const code = (piece.color === 'w' ? 'w' : 'b') + PIECE_TYPE_LETTER[piece.type];
    return '/static/pieces/cburnett/' + code + '.svg';
  }

  function squareName(file, rank) {
    // file/rank are 0-based, rank 0 = rank "1"
    return FILES[file] + (rank + 1);
  }

  function createPuzzleBoard(container, opts) {
    opts = opts || {};
    const chess = new Chess(opts.fen && opts.fen !== 'start' ? opts.fen : undefined);
    let orientation = opts.orientation === 'black' ? 'black' : 'white';
    let interactive = opts.interactive !== false;
    let selected = null;
    let legalTargets = [];
    let lastMove = null;
    const onAttemptMove = typeof opts.onAttemptMove === 'function' ? opts.onAttemptMove : function () {};

    container.classList.add('gpb-board');
    container.innerHTML = '';
    const squaresEls = {};

    function squareOrder() {
      // Rangs 8→1, fichiers a→h pour l'orientation blanche ; inversé si noir en bas.
      const ranks = orientation === 'white' ? [7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7];
      const files = orientation === 'white' ? [0, 1, 2, 3, 4, 5, 6, 7] : [7, 6, 5, 4, 3, 2, 1, 0];
      const order = [];
      ranks.forEach(r => files.forEach(f => order.push([f, r])));
      return order;
    }

    function buildGrid() {
      container.innerHTML = '';
      const files = orientation === 'white' ? FILES : [...FILES].reverse();
      const ranks = orientation === 'white' ? [8, 7, 6, 5, 4, 3, 2, 1] : [1, 2, 3, 4, 5, 6, 7, 8];
      squareOrder().forEach(([f, r], i) => {
        const sq = squareName(f, r);
        const cell = document.createElement('button');
        cell.type = 'button';
        cell.className = 'gpb-square ' + ((f + r) % 2 === 0 ? 'gpb-dark' : 'gpb-light');
        cell.dataset.square = sq;
        // Coordonnées : lettres sur la dernière rangée (bas), chiffres sur la
        // première colonne (gauche) — comme un vrai plateau physique.
        const col = i % 8, row = Math.floor(i / 8);
        if (row === 7) { const f2 = document.createElement('span'); f2.className = 'gpb-coord gpb-coord-file'; f2.textContent = files[col]; cell.appendChild(f2); }
        if (col === 0) { const r2 = document.createElement('span'); r2.className = 'gpb-coord gpb-coord-rank'; r2.textContent = ranks[row]; cell.appendChild(r2); }
        const pieceEl = document.createElement('span');
        pieceEl.className = 'gpb-piece';
        cell.appendChild(pieceEl);
        cell.addEventListener('click', () => onSquareClick(sq));
        // .gpb-piece has pointer-events:none (so it never swallows clicks
        // meant for the square button) — the drag listener goes on the
        // square itself, not the piece span.
        cell.addEventListener('pointerdown', (ev) => onPieceDragStart(ev, sq));
        container.appendChild(cell);
        squaresEls[sq] = cell;
      });
    }

    function render() {
      const board = chess.board(); // [8][8], board[0] = rank 8
      Object.keys(squaresEls).forEach(sq => {
        const cell = squaresEls[sq];
        const file = FILES.indexOf(sq[0]);
        const rank = parseInt(sq[1], 10) - 1;
        const piece = board[7 - rank][file];
        const pieceEl = cell.querySelector('.gpb-piece');
        if (pieceEl) {
          const url = pieceImageUrl(piece);
          pieceEl.style.backgroundImage = url ? `url(${url})` : 'none';
        }
        cell.classList.toggle('gpb-white-piece', !!piece && piece.color === 'w');
        cell.classList.toggle('gpb-black-piece', !!piece && piece.color === 'b');
        cell.classList.remove('gpb-selected', 'gpb-legal', 'gpb-legal-capture', 'gpb-last-move');
      });
      if (lastMove) {
        squaresEls[lastMove.from] && squaresEls[lastMove.from].classList.add('gpb-last-move');
        squaresEls[lastMove.to] && squaresEls[lastMove.to].classList.add('gpb-last-move');
      }
      if (selected) squaresEls[selected] && squaresEls[selected].classList.add('gpb-selected');
      legalTargets.forEach(t => {
        const cell = squaresEls[t.to];
        if (!cell) return;
        cell.classList.add(t.captured ? 'gpb-legal-capture' : 'gpb-legal');
      });
    }

    function clearSelection() {
      selected = null;
      legalTargets = [];
    }

    // Partagé entre le clic (sélectionner puis cliquer la cible) et le
    // drag & drop (lâcher directement sur la cible) — même règle de
    // légalité, même callback vers l'appelant dans les deux cas.
    function tryMoveTo(from, to) {
      const targets = chess.moves({ square: from, verbose: true });
      const target = targets.find(t => t.to === to);
      if (!target) return false;
      let promotion;
      if (target.flags && target.flags.indexOf('p') !== -1) promotion = 'q';
      onAttemptMove(from, to, promotion);
      return true;
    }

    function onSquareClick(sq) {
      if (!interactive) return;
      if (selected) {
        if (selected === sq) { clearSelection(); render(); return; }
        if (tryMoveTo(selected, sq)) { clearSelection(); render(); return; }
      }
      const piece = chess.get(sq);
      if (piece && piece.color === chess.turn()) {
        selected = sq;
        legalTargets = chess.moves({ square: sq, verbose: true });
        render();
      } else {
        clearSelection();
        render();
      }
    }

    // ── Drag & drop (souris + tactile, via Pointer Events) ────────────
    // Un pointerdown seul ne doit PAS déclencher le drag : sinon un simple
    // tap créerait un fantôme puis le 'click' natif qui suit désélectionnerait
    // aussitôt (double traitement). Le drag ne s'active qu'après un léger
    // déplacement — en dessous du seuil, c'est un tap normal et le handler
    // 'click' existant s'en charge sans rien changer à son comportement.
    const DRAG_THRESHOLD = 4; // px
    let pending = null;   // {from, startX, startY, pointerId} — avant seuil
    let dragState = null; // {from, ghostEl, size} — une fois le drag activé

    function squareAtPoint(clientX, clientY) {
      const rect = container.getBoundingClientRect();
      const size = rect.width / 8;
      let col = Math.floor((clientX - rect.left) / size);
      let row = Math.floor((clientY - rect.top) / size);
      if (col < 0 || col > 7 || row < 0 || row > 7) return null;
      const files = orientation === 'white' ? FILES : [...FILES].reverse();
      const ranks = orientation === 'white' ? [8, 7, 6, 5, 4, 3, 2, 1] : [1, 2, 3, 4, 5, 6, 7, 8];
      return files[col] + ranks[row];
    }

    function onPieceDragStart(ev, sq) {
      if (!interactive || ev.button === 2) return;
      const piece = chess.get(sq);
      if (!piece || piece.color !== chess.turn()) return;
      pending = { from: sq, startX: ev.clientX, startY: ev.clientY, pointerId: ev.pointerId };
      window.addEventListener('pointermove', onPieceDragMove);
      window.addEventListener('pointerup', onPieceDragEnd);
      window.addEventListener('pointercancel', onDragCancel);
    }

    function activateDrag(ev) {
      const { from } = pending;
      const piece = chess.get(from);
      selected = from;
      legalTargets = chess.moves({ square: from, verbose: true });
      render();
      const url = pieceImageUrl(piece);
      const ghostEl = document.createElement('div');
      ghostEl.className = 'gpb-drag-ghost';
      ghostEl.style.backgroundImage = `url(${url})`;
      const rect = container.getBoundingClientRect();
      const size = rect.width / 8;
      ghostEl.style.width = ghostEl.style.height = size + 'px';
      document.body.appendChild(ghostEl);
      dragState = { from, ghostEl, size };
      squaresEls[from].classList.add('gpb-dragging-source');
      positionGhost(ev.clientX, ev.clientY);
    }

    function positionGhost(clientX, clientY) {
      if (!dragState) return;
      const half = dragState.size / 2;
      dragState.ghostEl.style.left = (clientX - half) + 'px';
      dragState.ghostEl.style.top = (clientY - half) + 'px';
    }

    function onPieceDragMove(ev) {
      if (!pending) return;
      if (!dragState) {
        const dx = ev.clientX - pending.startX, dy = ev.clientY - pending.startY;
        if (Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
        ev.preventDefault();
        activateDrag(ev);
        return;
      }
      positionGhost(ev.clientX, ev.clientY);
      const hoverSq = squareAtPoint(ev.clientX, ev.clientY);
      Object.values(squaresEls).forEach(c => c.classList.remove('gpb-drop-hover'));
      if (hoverSq && legalTargets.some(t => t.to === hoverSq)) {
        squaresEls[hoverSq].classList.add('gpb-drop-hover');
      }
    }

    function cleanupDrag() {
      window.removeEventListener('pointermove', onPieceDragMove);
      window.removeEventListener('pointerup', onPieceDragEnd);
      window.removeEventListener('pointercancel', onDragCancel);
      pending = null;
    }

    function onDragCancel() {
      if (dragState) {
        dragState.ghostEl.remove();
        squaresEls[dragState.from] && squaresEls[dragState.from].classList.remove('gpb-dragging-source');
        Object.values(squaresEls).forEach(c => c.classList.remove('gpb-drop-hover'));
        clearSelection();
        render();
      }
      dragState = null;
      cleanupDrag();
    }

    function onPieceDragEnd(ev) {
      if (!pending) return;
      if (!dragState) {
        // Jamais franchi le seuil : c'était un tap, pas un drag — on laisse
        // le 'click' natif qui va suivre gérer la sélection normalement.
        cleanupDrag();
        return;
      }
      const { from, ghostEl } = dragState;
      const dropSq = squareAtPoint(ev.clientX, ev.clientY);
      ghostEl.remove();
      squaresEls[from] && squaresEls[from].classList.remove('gpb-dragging-source');
      Object.values(squaresEls).forEach(c => c.classList.remove('gpb-drop-hover'));
      dragState = null;
      cleanupDrag();
      if (dropSq && dropSq !== from && tryMoveTo(from, dropSq)) {
        clearSelection();
        render();
        return;
      }
      // Lâché hors case légale (ou sur la case de départ) : on garde la
      // pièce sélectionnée, comme après un clic simple sur elle.
      render();
    }

    function ensureArrowLayer() {
      let svg = container.querySelector('.gpb-arrows');
      if (!svg) {
        svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'gpb-arrows');
        svg.setAttribute('viewBox', '0 0 8 8');
        container.appendChild(svg);
      }
      return svg;
    }

    function squareCenter(sq) {
      const file = FILES.indexOf(sq[0]);
      const rank = parseInt(sq[1], 10) - 1;
      const col = orientation === 'white' ? file : 7 - file;
      const row = orientation === 'white' ? 7 - rank : rank;
      return { x: col + 0.5, y: row + 0.5 };
    }

    buildGrid();
    render();

    return {
      setFen(fen, moveJustPlayed) {
        // 'start' is accepted by the constructor (falls back to chess.js's
        // own default) but NOT by chess.load(fen) itself — passing the
        // literal string 'start' here silently failed and left the board
        // showing whatever position it had before, with no pieces movable
        // whenever a caller reset to the initial position mid-session.
        chess.load(fen && fen !== 'start' ? fen : DEFAULT_FEN);
        if (moveJustPlayed) {
          lastMove = moveJustPlayed;
        } else if (chess.history().length === 0) {
          // Repartir de la position de départ (nouvelle tentative, nouvelle
          // démo) sans qu'aucun coup n'ait encore été joué dessus : l'ancien
          // surlignage "dernier coup" d'une tentative précédente n'a plus de
          // sens ici et doit disparaître — sinon il restait affiché sur les
          // mêmes cases après le reset, même en tout début de partie.
          lastMove = null;
        }
        // (sinon : setFen appelé après une correction en cours de ligne —
        // on garde le surlignage du dernier VRAI coup joué, pas de raison
        // de l'effacer juste parce qu'un essai a été annulé.)
        clearSelection();
        // A guide/hint arrow is only ever meant to point at the move to play
        // from the position it was drawn on — once the position changes the
        // old arrow is stale and must not survive into the next position
        // (this is what left a hint arrow stuck on screen across variants).
        const svg = container.querySelector('.gpb-arrows');
        if (svg) svg.innerHTML = '';
        render();
      },
      setPosition(fenOrChess) {
        this.setFen(fenOrChess);
      },
      setLastMove(from, to) {
        lastMove = from && to ? { from, to } : null;
        render();
      },
      applyMove(from, to, promotion) {
        const mv = chess.move({ from, to, promotion: promotion || 'q' });
        if (mv) lastMove = { from, to };
        clearSelection();
        render();
        return mv;
      },
      getFen() { return chess.fen(); },
      getChess() { return chess; },
      setInteractive(v) { interactive = !!v; if (!interactive) { clearSelection(); render(); } },
      setOrientation(o) { orientation = o === 'black' ? 'black' : 'white'; buildGrid(); render(); },
      flashSquare(sq, kind) {
        const cell = squaresEls[sq];
        if (!cell) return;
        cell.classList.add(kind === 'bad' ? 'gpb-flash-bad' : 'gpb-flash-good');
        setTimeout(() => cell.classList.remove('gpb-flash-good', 'gpb-flash-bad'), 550);
      },
      showArrow(from, to) {
        const svg = ensureArrowLayer();
        svg.innerHTML = '';
        const a = squareCenter(from), b = squareCenter(to);
        const marker = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
        marker.innerHTML = '<marker id="gpb-arrowhead" markerWidth="3" markerHeight="3" refX="1.4" refY="1.5" orient="auto"><path d="M0,0 L3,1.5 L0,3 Z" fill="var(--gpb-arrow,#b58863)"/></marker>';
        svg.appendChild(marker);
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', a.x); line.setAttribute('y1', a.y);
        line.setAttribute('x2', b.x); line.setAttribute('y2', b.y);
        line.setAttribute('stroke', 'var(--gpb-arrow,#b58863)');
        line.setAttribute('stroke-width', '0.14');
        line.setAttribute('stroke-linecap', 'round');
        line.setAttribute('opacity', '0.85');
        line.setAttribute('marker-end', 'url(#gpb-arrowhead)');
        svg.appendChild(line);
      },
      clearArrows() {
        const svg = container.querySelector('.gpb-arrows');
        if (svg) svg.innerHTML = '';
      },
      destroy() {
        if (dragState) { dragState.ghostEl.remove(); dragState = null; }
        cleanupDrag();
        container.innerHTML = '';
      },
    };
  }

  global.createPuzzleBoard = createPuzzleBoard;
})(window);
