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
  const WHITE_GLYPH = { p: '♙', n: '♘', b: '♗', r: '♖', q: '♕', k: '♔' };
  const BLACK_GLYPH = { p: '♟', n: '♞', b: '♝', r: '♜', q: '♛', k: '♚' };
  const FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];

  function glyphFor(piece) {
    if (!piece) return '';
    const map = piece.color === 'w' ? WHITE_GLYPH : BLACK_GLYPH;
    return map[piece.type] || '';
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
      squareOrder().forEach(([f, r]) => {
        const sq = squareName(f, r);
        const cell = document.createElement('button');
        cell.type = 'button';
        cell.className = 'gpb-square ' + ((f + r) % 2 === 0 ? 'gpb-dark' : 'gpb-light');
        cell.dataset.square = sq;
        cell.addEventListener('click', () => onSquareClick(sq));
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
        cell.textContent = glyphFor(piece);
        cell.classList.toggle('gpb-white-piece', !!piece && piece.color === 'w');
        cell.classList.toggle('gpb-black-piece', !!piece && piece.color === 'b');
        cell.classList.remove('gpb-selected', 'gpb-legal', 'gpb-legal-capture');
      });
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

    function onSquareClick(sq) {
      if (!interactive) return;
      if (selected) {
        const target = legalTargets.find(t => t.to === sq);
        if (target) {
          let promotion;
          if (target.flags && target.flags.indexOf('p') !== -1) promotion = 'q';
          onAttemptMove(selected, sq, promotion);
          clearSelection();
          render();
          return;
        }
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
      setFen(fen) {
        chess.load(fen);
        clearSelection();
        render();
      },
      setPosition(fenOrChess) {
        this.setFen(fenOrChess);
      },
      applyMove(from, to, promotion) {
        const mv = chess.move({ from, to, promotion: promotion || 'q' });
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
        container.innerHTML = '';
      },
    };
  }

  global.createPuzzleBoard = createPuzzleBoard;
})(window);
