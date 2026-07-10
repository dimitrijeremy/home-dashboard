import React, { useEffect, useRef, useState } from 'react'
import Hls from 'hls.js'
import { ptzCommand } from '../../services/api'

const DEFAULT_VIDEO_SIZE = { width: 1920, height: 1080 }

// ── PTZ pad ────────────────────────────────────────────────────────────────
// Tekan-tahan mengirim ptz start, lepas mengirim stop (Dahua ptz.cgi via backend).
function PTZPad({ camId }) {
  const activeRef = useRef(null)
  const [err, setErr] = useState(null)
  const errTimer = useRef(null)

  const showErr = (msg) => {
    setErr(msg)
    clearTimeout(errTimer.current)
    errTimer.current = setTimeout(() => setErr(null), 3000)
  }

  const start = (code) => {
    activeRef.current = code
    ptzCommand(camId, 'start', code).catch(e => showErr(e.message))
  }
  const stop = () => {
    const code = activeRef.current
    if (!code) return
    activeRef.current = null
    ptzCommand(camId, 'stop', code).catch(() => {})
  }

  useEffect(() => () => { stop(); clearTimeout(errTimer.current) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const btnStyle = {
    width: 30, height: 30, display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'rgba(0,0,0,.55)', color: '#fff', border: '1px solid rgba(255,255,255,.25)',
    borderRadius: 6, cursor: 'pointer', fontSize: 13, userSelect: 'none', touchAction: 'none',
  }
  const btn = (code, label, title) => (
    <button
      type="button"
      title={title}
      style={btnStyle}
      onPointerDown={(e) => { e.preventDefault(); start(code) }}
      onPointerUp={stop}
      onPointerLeave={stop}
      onPointerCancel={stop}
      onContextMenu={(e) => e.preventDefault()}
    >{label}</button>
  )

  return (
    <div style={{
      position: 'absolute', right: 10, bottom: 10, zIndex: 5,
      display: 'flex', gap: 6, alignItems: 'flex-end',
    }}>
      {err && (
        <div style={{
          background: 'rgba(239,68,68,.9)', color: '#fff', fontSize: '.68rem',
          padding: '4px 8px', borderRadius: 6, maxWidth: 180, alignSelf: 'center',
        }}>{err}</div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 30px)', gap: 4 }}>
        <span />{btn('Up', '▲', 'Atas')}<span />
        {btn('Left', '◀', 'Kiri')}<span />{btn('Right', '▶', 'Kanan')}
        <span />{btn('Down', '▼', 'Bawah')}<span />
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {btn('ZoomTele', '＋', 'Zoom in')}
        {btn('ZoomWide', '－', 'Zoom out')}
      </div>
      <div style={{
        position: 'absolute', right: 0, top: -20, whiteSpace: 'nowrap',
        fontSize: '.6rem', color: 'rgba(255,255,255,.75)',
        textShadow: '0 1px 2px rgba(0,0,0,.8)',
      }}>
        ⏱ video tertunda ±5–10 dtk dari gerakan asli
      </div>
    </div>
  )
}

function normalizeStreamUrl(src) {
  return src
}

function parseZonePoints(zone) {
  if (Array.isArray(zone?.points)) return zone.points
  if (typeof zone?.points_json !== 'string') return []
  try {
    const parsed = JSON.parse(zone.points_json)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function toSvgPoints(points, videoSize) {
  return points
    .filter((point) => Array.isArray(point) && point.length >= 2)
    .map(([x, y]) => `${Math.round(x * videoSize.width)},${Math.round(y * videoSize.height)}`)
    .join(' ')
}

// mediamtx always outputs LL-HLS v10 (EXT-X-PART-INF, EXT-X-SERVER-CONTROL,
// EXT-X-PRELOAD-HINT). With lowLatencyMode:true hls.js sends ?_HLS_msn=X&_HLS_part=Y
// blocked-reload requests — if these timeout it enters a ~30s retry → fatal cycle.
// Solution: strip LL-HLS tags before hls.js parses the playlist. hls.js then
// treats it as standard HLS, polls segments normally, no blocked-reload at all.
function makeStripLLHlsLoader(HlsClass) {
  const Base = HlsClass.DefaultConfig.loader
  return class StripLLHlsLoader extends Base {
    load(context, config, callbacks) {
      const origOnSuccess = callbacks.onSuccess
      callbacks = Object.assign({}, callbacks, {
        onSuccess(response, stats, ctx, networkDetails) {
          if (typeof response.data === 'string') {
            response.data = response.data
              .replace(/^#EXT-X-PART(?:-INF)?:.*\r?\n?/gm, '')
              .replace(/^#EXT-X-SERVER-CONTROL:.*\r?\n?/gm, '')
              .replace(/^#EXT-X-PRELOAD-HINT:.*\r?\n?/gm, '')
              .replace(/#EXT-X-VERSION:\d+/, '#EXT-X-VERSION:6')
          }
          origOnSuccess(response, stats, ctx, networkDetails)
        },
      })
      super.load(context, config, callbacks)
    }
  }
}

const HLS_CONFIG = {
  enableWorker: true,
  lowLatencyMode: false,        // use standard HLS — LL tags stripped by loader above
  liveSyncDurationCount: 3,     // buffer 3 segments (~13s) behind live edge
  liveMaxLatencyDurationCount: 10,
  maxBufferLength: 20,
  maxBufferHole: 0.5,
  startLevel: -1,
  loader: makeStripLLHlsLoader(Hls),
  manifestLoadPolicy: {
    default: {
      maxTimeToFirstByteMs: 15000,
      maxLoadTimeMs: 20000,
      timeoutRetry: { maxNumRetry: 2, retryDelayMs: 1000, maxRetryDelayMs: 4000 },
      errorRetry:   { maxNumRetry: 3, retryDelayMs: 2000, maxRetryDelayMs: 8000 },
    },
  },
  playlistLoadPolicy: {
    default: {
      maxTimeToFirstByteMs: 10000,
      maxLoadTimeMs: 20000,
      timeoutRetry: { maxNumRetry: 2, retryDelayMs: 1000, maxRetryDelayMs: 4000 },
      errorRetry:   { maxNumRetry: 6, retryDelayMs: 1000, maxRetryDelayMs: 8000 },
    },
  },
}

const MAX_RETRIES = 5

export default function CCTVPlayer({ camId, src, name, zones = [], showZones = false, onRemove, removable, onEdit, ptzSupported = false }) {
  const videoRef = useRef(null)
  const hlsRef   = useRef(null)
  const retryRef = useRef(null)
  const deadRef  = useRef(false)
  const recoverRef = useRef(0)
  const retryCountRef = useRef(0)
  const [status, setStatus] = useState('loading')
  const [muted, setMuted]   = useState(true)
  const [errorMsg, setErrorMsg] = useState(null)
  const [retryKey, setRetryKey] = useState(0)
  const [videoSize, setVideoSize] = useState(DEFAULT_VIDEO_SIZE)
  const [showPtz, setShowPtz] = useState(false)

  useEffect(() => {
    const video = videoRef.current
    if (!video || !src) return
    const effectiveSrc = normalizeStreamUrl(src)

    deadRef.current = false
    recoverRef.current = 0
    retryCountRef.current = 0

    function cleanup() {
      clearTimeout(retryRef.current)
      video.onloadedmetadata = null
      video.onloadeddata = null
      video.oncanplay = null
      video.oncanplaythrough = null
      video.onplaying = null
      video.ontimeupdate = null
      if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null }
    }

    function attachDead(msg) {
      deadRef.current = true
      cleanup()
      setErrorMsg(msg)
      setStatus('dead')
    }

    function attach() {
      if (deadRef.current) return
      cleanup()
      setStatus('loading')

      // Only reset muted to true on the very first attach.
      // Subsequent retries preserve whatever the user chose.
      if (retryCountRef.current === 0) {
        video.muted = true
        video.setAttribute('muted', '')
      }

      const requestPlay = () => {
        if (deadRef.current || !video.paused || !video.srcObject && video.readyState < 2) return
        video.play().catch(() => {})
      }

      const syncVideoSize = () => {
        if (!video.videoWidth || !video.videoHeight) return
        setVideoSize((current) => {
          if (current.width === video.videoWidth && current.height === video.videoHeight) {
            return current
          }
          return { width: video.videoWidth, height: video.videoHeight }
        })
      }

      const markLive = () => {
        if (deadRef.current) return
        syncVideoSize()
        recoverRef.current = 0
        setStatus('live')
      }

      video.onloadedmetadata = syncVideoSize
      video.onloadeddata = requestPlay
      video.oncanplay = requestPlay
      video.oncanplaythrough = requestPlay
      video.onplaying = markLive
      video.ontimeupdate = () => {
        if (!deadRef.current && video.currentTime > 0) markLive()
      }

      if (Hls.isSupported()) {
        const hls = new Hls(HLS_CONFIG)
        hlsRef.current = hls

        // Safe order: attachMedia first so MANIFEST_PARSED fires after
        // MediaSource is ready, then play() in MANIFEST_PARSED is safe.
        hls.attachMedia(video)
        hls.on(Hls.Events.MEDIA_ATTACHED, () => { hls.loadSource(effectiveSrc) })

        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          if (deadRef.current) return
          // Ask the browser to start playback, but only mark the stream live once
          // loadeddata/playing fires. MANIFEST_PARSED itself is too early and can
          // leave the user staring at a black frame during initial buffering.
          requestPlay()
        })

        hls.on(Hls.Events.FRAG_BUFFERED, () => {
          requestPlay()
        })

        hls.on(Hls.Events.ERROR, (_, data) => {
          // Non-fatal: hls.js handles internally via LoadPolicy retries. Ignore.
          if (!data.fatal || deadRef.current) return

          if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
            // MEDIA_ERROR is often recoverable — try fix before destroying.
            if (recoverRef.current < 2) {
              recoverRef.current += 1
              hls.recoverMediaError()
              return
            }
          } else if (data.response?.code === 404) {
            // Stream not published yet (e.g. ch1 no camera).
            // Slow retry — don't hammer mediamtx with rapid reconnects.
            if (retryCountRef.current >= MAX_RETRIES) {
              attachDead('Stream tidak tersedia setelah beberapa percobaan (404)')
              return
            }
            retryCountRef.current += 1
            cleanup()
            setStatus('loading')
            retryRef.current = setTimeout(attach, 8000)
            return
          } else if (data.type === Hls.ErrorTypes.NETWORK_ERROR && recoverRef.current < 2) {
            // A transient playlist/segment miss should not tear down the player.
            // Restart hls.js loading first; reattach only if that still fails.
            recoverRef.current += 1
            setStatus('loading')
            hls.startLoad(-1)
            return
          }

          // True fatal error after local recovery attempts are exhausted.
          if (retryCountRef.current >= MAX_RETRIES) {
            const msg = data.type === Hls.ErrorTypes.NETWORK_ERROR
              ? `Koneksi gagal (${data.response?.code ?? 'network error'})`
              : `Error stream (${data.details ?? data.type})`
            attachDead(msg)
            return
          }
          retryCountRef.current += 1
          cleanup()
          setStatus('loading')
          retryRef.current = setTimeout(attach, 6000)
        })
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        // Safari native HLS — autoPlay + muted handles play() automatically
        video.src = effectiveSrc
        video.load()
        video.addEventListener('canplay', () => { if (!deadRef.current) requestPlay() }, { once: true })
        video.addEventListener('error', () => {
          if (deadRef.current) return
          if (retryCountRef.current >= MAX_RETRIES) {
            attachDead('Stream tidak tersedia')
            return
          }
          retryCountRef.current += 1
          retryRef.current = setTimeout(attach, 4000)
        }, { once: true })
      } else {
        setStatus('error')
      }
    }

    attach()
    return () => { deadRef.current = true; cleanup() }
  }, [src, retryKey])

  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.muted = muted
      if (!muted) videoRef.current.removeAttribute('muted')
      else videoRef.current.setAttribute('muted', '')
    }
  }, [muted])

  const toggleFullscreen = () => {
    const el = videoRef.current
    if (!el) return
    document.fullscreenElement ? document.exitFullscreen() : el.requestFullscreen?.()
  }

  const overlayZones = showZones
    ? zones
        .map((zone) => ({ ...zone, parsedPoints: parseZonePoints(zone) }))
        .filter((zone) => zone.parsedPoints.length >= 3)
    : []

  return (
    <div className="cam-card">
      <div className="cam-header">
        <div className="cam-title">
          <span className={`cam-status ${status}`} />
          {name}
        </div>
        <div className="cam-actions">
          {camId != null && ptzSupported && (
            <button
              className="btn-icon"
              onClick={() => setShowPtz(p => !p)}
              title={showPtz ? 'Sembunyikan kontrol PTZ' : 'Kontrol PTZ'}
              style={showPtz ? { color: 'var(--accent)' } : undefined}
            >🕹</button>
          )}
          {onEdit && (
            <button className="btn-icon" onClick={onEdit} title="Edit channel">✎</button>
          )}
          <button className="btn-icon" onClick={() => setMuted(m => !m)} title={muted ? 'Unmute' : 'Mute'}>
            {muted ? '🔇' : '🔊'}
          </button>
          <button className="btn-icon" onClick={toggleFullscreen} title="Fullscreen">⛶</button>
          {removable && (
            <button className="btn-icon" onClick={onRemove} title="Hapus channel" style={{color:'var(--red)'}}>✕</button>
          )}
        </div>
      </div>
      <div className="cam-video">
        <video
          ref={(el) => {
            videoRef.current = el
            if (el && !hlsRef.current) {
              // Only set muted on first mount (before any stream attaches)
              el.muted = true
              el.setAttribute('muted', '')
            }
          }}
          autoPlay
          playsInline
        />
        {showPtz && camId != null && ptzSupported && <PTZPad camId={camId} />}
        {overlayZones.length > 0 && (
          <div className="cam-zone-overlay">
            <svg viewBox={`0 0 ${videoSize.width} ${videoSize.height}`} preserveAspectRatio="xMidYMid slice" aria-hidden="true">
              {overlayZones.map((zone, index) => {
                const labelPoint = zone.parsedPoints[0]
                return (
                  <g key={zone.id || `${zone.name}-${index}`}>
                    <polygon className="cam-zone-polygon" points={toSvgPoints(zone.parsedPoints, videoSize)} />
                    {labelPoint && (
                      <text
                        className="cam-zone-label"
                        x={Math.round(labelPoint[0] * videoSize.width)}
                        y={Math.max(26, Math.round(labelPoint[1] * videoSize.height) - 10)}
                      >
                        {zone.name}
                      </text>
                    )}
                  </g>
                )
              })}
            </svg>
          </div>
        )}
        {status === 'loading' && (
          <div className="cam-overlay"><span className="cam-spinner" /></div>
        )}
        {status === 'error' && (
          <div className="cam-error">
            <span className="cam-error-icon">📷</span>
            <span>Browser tidak mendukung HLS</span>
          </div>
        )}
        {status === 'dead' && (
          <div className="cam-error">
            <span className="cam-error-icon">⚠️</span>
            <span>{errorMsg || 'Stream tidak tersedia'}</span>
            <button
              className="btn-icon"
              style={{ marginTop: '8px', fontSize: '12px', padding: '4px 10px' }}
              onClick={() => {
                retryCountRef.current = 0
                deadRef.current = false
                setErrorMsg(null)
                const video = videoRef.current
                if (video) { video.muted = true; video.setAttribute('muted', '') }
                setStatus('loading')
                setMuted(true)
                // Re-trigger the effect by forcing a re-attach via a local attach call
                // We can't call attach() here (it's scoped inside useEffect),
                // so we bump a separate state to force effect re-run.
                setRetryKey(k => k + 1)
              }}
            >↺ Coba lagi</button>
          </div>
        )}
      </div>
    </div>
  )
}
