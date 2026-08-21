// Minimal QR code generator with no dependencies.
// Byte mode, error-correction level M, versions 1-10 (up to ~213 bytes),
// mask chosen by penalty score. Renders to an SVG string.
// Exposed as window.CooksterQR.toSvg(text, size).

(function (root) {
  // GF(256) arithmetic for Reed-Solomon (primitive polynomial 0x11D).
  const EXP = new Uint8Array(512)
  const LOG = new Uint8Array(256)
  ;(function initGF() {
    let x = 1
    for (let i = 0; i < 255; i++) {
      EXP[i] = x
      LOG[x] = i
      x <<= 1
      if (x & 0x100) x ^= 0x11d
    }
    for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255]
  })()

  function gfMul(a, b) {
    return a === 0 || b === 0 ? 0 : EXP[LOG[a] + LOG[b]]
  }

  // Reed-Solomon generator polynomial of the given degree.
  function rsDivisor(degree) {
    const result = new Array(degree).fill(0)
    result[degree - 1] = 1
    let rootEl = 1
    for (let i = 0; i < degree; i++) {
      for (let j = 0; j < degree; j++) {
        result[j] = gfMul(result[j], rootEl)
        if (j + 1 < degree) result[j] ^= result[j + 1]
      }
      rootEl = gfMul(rootEl, 2)
    }
    return result
  }

  function rsRemainder(data, divisor) {
    const result = new Array(divisor.length).fill(0)
    data.forEach(b => {
      const factor = b ^ result.shift()
      result.push(0)
      divisor.forEach((coef, i) => { result[i] ^= gfMul(coef, factor) })
    })
    return result
  }

  // ECC level M parameters per version:
  // [blocks in group 1, data codewords per group-1 block,
  //  blocks in group 2, data codewords per group-2 block, ecc codewords per block]
  const ECC_M = {
    1: [1, 16, 0, 0, 10],
    2: [1, 28, 0, 0, 16],
    3: [1, 44, 0, 0, 26],
    4: [2, 32, 0, 0, 18],
    5: [2, 43, 0, 0, 24],
    6: [4, 27, 0, 0, 16],
    7: [4, 31, 0, 0, 18],
    8: [2, 38, 2, 39, 22],
    9: [3, 36, 2, 37, 22],
    10: [4, 43, 1, 44, 26]
  }

  // Alignment pattern centre coordinates per version.
  const ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]
  }

  const MASKS = [
    (x, y) => (x + y) % 2 === 0,
    (x, y) => y % 2 === 0,
    (x, y) => x % 3 === 0,
    (x, y) => (x + y) % 3 === 0,
    (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
    (x, y) => (x * y) % 2 + (x * y) % 3 === 0,
    (x, y) => ((x * y) % 2 + (x * y) % 3) % 2 === 0,
    (x, y) => ((x + y) % 2 + (x * y) % 3) % 2 === 0
  ]

  function getBit(x, i) {
    return ((x >>> i) & 1) !== 0
  }

  function encode(text) {
    const bytes = Array.from(new TextEncoder().encode(text))

    // Pick the smallest version that fits (byte mode, ECC M).
    let version = 0
    for (let v = 1; v <= 10; v++) {
      const [nb1, dc1, nb2, dc2] = ECC_M[v]
      const capacityBits = (nb1 * dc1 + nb2 * dc2) * 8
      const countBits = v < 10 ? 8 : 16
      if (4 + countBits + bytes.length * 8 <= capacityBits) { version = v; break }
    }
    if (!version) throw new Error('Text too long for QR code')

    const [nb1, dc1, nb2, dc2, ecLen] = ECC_M[version]
    const totalDataCw = nb1 * dc1 + nb2 * dc2

    // Build the data bit stream: mode (0100), length, bytes, terminator, padding.
    const bits = []
    const appendBits = (val, len) => { for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1) }
    appendBits(4, 4)
    appendBits(bytes.length, version < 10 ? 8 : 16)
    bytes.forEach(b => appendBits(b, 8))
    appendBits(0, Math.min(4, totalDataCw * 8 - bits.length))
    while (bits.length % 8 !== 0) bits.push(0)
    const dataCw = []
    for (let i = 0; i < bits.length; i += 8) {
      dataCw.push(bits.slice(i, i + 8).reduce((acc, b) => (acc << 1) | b, 0))
    }
    for (let pad = 0; dataCw.length < totalDataCw; pad++) {
      dataCw.push(pad % 2 === 0 ? 0xec : 0x11)
    }

    // Split into blocks, compute ECC, interleave.
    const divisor = rsDivisor(ecLen)
    const blocks = []
    let offset = 0
    ;[[nb1, dc1], [nb2, dc2]].forEach(([count, dcw]) => {
      for (let i = 0; i < count; i++) {
        const data = dataCw.slice(offset, offset + dcw)
        offset += dcw
        blocks.push({ data, ecc: rsRemainder(data, divisor) })
      }
    })
    const codewords = []
    const maxData = Math.max(dc1, dc2)
    for (let i = 0; i < maxData; i++) blocks.forEach(b => { if (i < b.data.length) codewords.push(b.data[i]) })
    for (let i = 0; i < ecLen; i++) blocks.forEach(b => codewords.push(b.ecc[i]))

    // Assemble the module matrix.
    const size = 17 + 4 * version
    const modules = Array.from({ length: size }, () => new Array(size).fill(false))
    const isFunc = Array.from({ length: size }, () => new Array(size).fill(false))
    const setFunc = (x, y, dark) => { modules[y][x] = dark; isFunc[y][x] = true }

    const drawFinder = (cx, cy) => {
      for (let dy = -1; dy <= 7; dy++) {
        for (let dx = -1; dx <= 7; dx++) {
          const x = cx + dx
          const y = cy + dy
          if (x < 0 || x >= size || y < 0 || y >= size) continue
          const inRing = dx >= 0 && dx <= 6 && dy >= 0 && dy <= 6
          const dark = inRing && (dx === 0 || dx === 6 || dy === 0 || dy === 6 ||
            (dx >= 2 && dx <= 4 && dy >= 2 && dy <= 4))
          setFunc(x, y, dark)
        }
      }
    }
    drawFinder(0, 0)
    drawFinder(size - 7, 0)
    drawFinder(0, size - 7)

    for (let i = 8; i < size - 8; i++) {
      setFunc(i, 6, i % 2 === 0)
      setFunc(6, i, i % 2 === 0)
    }

    const pos = ALIGN[version]
    pos.forEach(cy => pos.forEach(cx => {
      if (isFunc[cy][cx]) return
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) {
          setFunc(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1)
        }
      }
    }))

    // Format info: ECC level M has format bits 00. Written into `grid` so the
    // reservation pass and the final pass can target different matrices.
    const drawFormatBits = (mask, grid) => {
      const put = (x, y, dark) => { grid[y][x] = dark; isFunc[y][x] = true }
      const data = mask // (0 << 3) | mask
      let rem = data
      for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537)
      const fmt = ((data << 10) | rem) ^ 0x5412
      for (let i = 0; i <= 5; i++) put(8, i, getBit(fmt, i))
      put(8, 7, getBit(fmt, 6))
      put(8, 8, getBit(fmt, 7))
      put(7, 8, getBit(fmt, 8))
      for (let i = 9; i < 15; i++) put(14 - i, 8, getBit(fmt, i))
      for (let i = 0; i < 8; i++) put(size - 1 - i, 8, getBit(fmt, i))
      for (let i = 8; i < 15; i++) put(8, size - 15 + i, getBit(fmt, i))
      put(8, size - 8, true) // always dark
    }
    drawFormatBits(0, modules) // reserve the cells before placing data

    if (version >= 7) {
      let rem = version
      for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25)
      const bits18 = (version << 12) | rem
      for (let i = 0; i < 18; i++) {
        const bit = getBit(bits18, i)
        const a = size - 11 + (i % 3)
        const b = Math.floor(i / 3)
        setFunc(a, b, bit)
        setFunc(b, a, bit)
      }
    }

    // Zigzag data placement from the bottom-right corner.
    let bitIdx = 0
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5
      for (let vert = 0; vert < size; vert++) {
        for (let j = 0; j < 2; j++) {
          const x = right - j
          const upward = ((right + 1) & 2) === 0
          const y = upward ? size - 1 - vert : vert
          if (!isFunc[y][x] && bitIdx < codewords.length * 8) {
            modules[y][x] = getBit(codewords[bitIdx >>> 3], 7 - (bitIdx & 7))
            bitIdx++
          }
        }
      }
    }

    // Try every mask, keep the one with the lowest penalty score.
    const baseModules = modules.map(row => row.slice())
    let best = null
    let bestMask = 0
    let bestPenalty = Infinity
    for (let mask = 0; mask < 8; mask++) {
      const masked = baseModules.map(row => row.slice())
      for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
          if (!isFunc[y][x] && MASKS[mask](x, y)) masked[y][x] = !masked[y][x]
        }
      }
      const penalty = penaltyScore(masked)
      if (penalty < bestPenalty) { bestPenalty = penalty; best = masked; bestMask = mask }
    }
    drawFormatBits(bestMask, best) // write the real format bits into the result
    return best
  }

  function penaltyScore(m) {
    const size = m.length
    let penalty = 0

    // Rule 1: runs of 5+ same-colour modules in a row or column.
    const lines = []
    for (let y = 0; y < size; y++) lines.push(m[y])
    for (let x = 0; x < size; x++) lines.push(m.map(row => row[x]))
    lines.forEach(line => {
      let run = 1
      for (let i = 1; i <= size; i++) {
        if (i < size && line[i] === line[i - 1]) {
          run++
        } else {
          if (run >= 5) penalty += run - 2
          run = 1
        }
      }
    })

    // Rule 2: 2x2 blocks of the same colour.
    for (let y = 0; y < size - 1; y++) {
      for (let x = 0; x < size - 1; x++) {
        const c = m[y][x]
        if (m[y][x + 1] === c && m[y + 1][x] === c && m[y + 1][x + 1] === c) penalty += 3
      }
    }

    // Rule 3: finder-like 1011101 patterns flanked by four light modules.
    lines.forEach(line => {
      const s = line.map(b => (b ? '1' : '0')).join('')
      for (let i = 0; i + 11 <= size; i++) {
        const slice = s.slice(i, i + 11)
        if (slice === '10111010000' || slice === '00001011101') penalty += 40
      }
    })

    // Rule 4: deviation from a 50% dark/light balance.
    let dark = 0
    m.forEach(row => row.forEach(b => { if (b) dark++ }))
    penalty += Math.floor(Math.abs(dark * 20 - size * size * 10) / (size * size)) * 10

    return penalty
  }

  function toSvg(text, size = 200, margin = 2) {
    const m = encode(text)
    const n = m.length
    const total = n + margin * 2
    let d = ''
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        if (m[y][x]) d += `M${x + margin} ${y + margin}h1v1h-1z`
      }
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${total} ${total}" width="${size}" height="${size}" shape-rendering="crispEdges" role="img" aria-label="Pairing QR code"><rect width="${total}" height="${total}" fill="#fff"/><path d="${d}" fill="#000"/></svg>`
  }

  root.CooksterQR = { toSvg }
})(window)
