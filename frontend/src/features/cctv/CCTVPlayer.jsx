import React, { useEffect, useRef, useState } from 'react'
import Hls from 'hls.js'
import { ptzCheck, ptzCommand, updateCamera, getCamera } from '../../services/api'

function normalizeStreamUrl(src) {
  return src
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

const MAX_RETRIES = 12   // ~24s of 2s retries before giving up

// ── PTZ Control Panel ──────────────────────────────────────────────────────
function PTZPanel({ camId, capable }) {
  const [error,    setError]  = useState(null)
  const pressing              = useRef(null)

  const send = (code, action = 'start') => {
    setError(null)
    ptzCommand(camId, action, code).catch(e => setError(e.message))
  }

  const startPress = (code) => {
    send(code, 'start')
    pressing.current = code
  }
  const stopPress = () => {
    if (pressing.current) { send(pressing.current, 'stop'); pressing.current = null }
  }

  if (capable === null) return (
    <div className="ptz-overlay">
      <span style={{ fontSize: '.7rem', color: 'var(--text-muted)' }}>Memeriksa PTZ...</span>
    </div>
  )
  if (capable === false) return (
    <div className="ptz-overlay">
      <span style={{ fontSize: '.7rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>PTZ tidak didukung kamera ini</span>
    </div>
  )

  const btn = (label, code, style = {}) => (
    <button
      className="btn-icon"
      style={{ fontSize: '1rem', padding: '6px 10px', borderRadius: 4, ...style }}
      onMouseDown={() => startPress(code)}
      onMouseUp={stopPress}
      onMouseLeave={stopPress}
      onTouchStart={e => { e.preventDefault(); startPress(code) }}
      onTouchEnd={stopPress}
      title={code}
    >{label}</button>
  )

  return (
    <div className="ptz-overlay">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,36px)', gap: 4, justifyContent: 'center' }}>
        <div />{btn('▲', 'Up')}<div />
        {btn('◀', 'Left')}{btn('⏹', 'Up', { visibility: 'hidden' })}{btn('▶', 'Right')}
        <div />{btn('▼', 'Down')}<div />
      </div>
      <div style={{ display: 'flex', gap: 6, marginTop: 6, justifyContent: 'center' }}>
        {btn('🔍+', 'ZoomTele')}
        {btn('🔍−', 'ZoomWide')}
      </div>
      {error && <div style={{ fontSize: '.65rem', color: 'var(--red)', marginTop: 4 }}>⚠ {error}</div>}
    </div>
  )
}

// ── Edit Camera Modal ──────────────────────────────────────────────────────
function EditCameraModal({ camId, initialName, onSave, onClose }) {
  const [loading,   setLoading]  = useState(true)
  const [saving,    setSaving]   = useState(false)
  const [error,     setError]    = useState('')
  const [details,   setDetails]  = useState(null)
  const [name,      setName]     = useState(initialName)
  const [ip,        setIp]       = useState('')
  const [port,      setPort]     = useState('554')
  const [username,  setUsername] = useState('')
  const [password,  setPassword] = useState('')
  const [channel,   setChannel]  = useState('1')
  const [showPass,  setShowPass] = useState(false)

  useEffect(() => {
    getCamera(camId)
      .then(d => {
        setDetails(d)
        setName(d.name)
        setIp(d.ip || '')
        setPort(String(d.port || 554))
        setUsername(d.username || '')
        setChannel(String(d.channel || 1))
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [camId])

  const submit = async () => {
    const n = name.trim()
    if (!n) { setError('Nama tidak boleh kosong'); return }
    if (details && !details.builtin && !ip.trim()) { setError('IP tidak boleh kosong'); return }
    setSaving(true)
    try {
      const body = { name: n }
      if (details && !details.builtin) {
        body.ip      = ip.trim()
        body.port    = parseInt(port, 10) || 554
        body.username = username.trim()
        if (password) body.password = password
        body.channel = parseInt(channel, 10) || 1
      }
      const updated = await updateCamera(camId, body)
      onSave(updated.name)
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const isCustom = details && !details.builtin

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" onKeyDown={e => { if (e.key === 'Escape') onClose() }} style={{ maxWidth: 360 }}>
        <div className="modal-title">✏️ Edit Kamera</div>

        {loading ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '.82rem', padding: '12px 0', textAlign: 'center' }}>
            Memuat data kamera...
          </div>
        ) : (
          <>
            <label>Nama Tampilan</label>
            <input
              autoFocus
              value={name}
              onChange={e => { setName(e.target.value); setError('') }}
            />

            {isCustom && (
              <>
                <div style={{ borderTop: '1px solid var(--border)', margin: '12px 0 10px', paddingTop: 10 }}>
                  <div style={{ fontSize: '.72rem', color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '.05em' }}>
                    Koneksi Kamera
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 90px', gap: 8, marginBottom: 10 }}>
                  <div>
                    <label style={{ fontSize: '.72rem' }}>IP Address</label>
                    <input
                      value={ip}
                      onChange={e => { setIp(e.target.value); setError('') }}
                      placeholder="10.10.80.2"
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: '.72rem' }}>Port</label>
                    <input
                      type="number"
                      value={port}
                      onChange={e => setPort(e.target.value)}
                      min="1" max="65535"
                    />
                  </div>
                </div>

                <label style={{ fontSize: '.72rem' }}>Username</label>
                <input
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  placeholder="dashboard"
                  style={{ marginBottom: 10 }}
                />

                <label style={{ fontSize: '.72rem' }}>Password</label>
                <div style={{ position: 'relative', marginBottom: 10 }}>
                  <input
                    type={showPass ? 'text' : 'password'}
                    value={password}
                    onChange={e => setPassword(e.target.value)}
                    placeholder="Kosongkan untuk tidak mengubah"
                    style={{ paddingRight: 36, width: '100%', boxSizing: 'border-box' }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPass(p => !p)}
                    style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: '.78rem', padding: 0 }}
                    tabIndex={-1}
                  >{showPass ? '🙈' : '👁️'}</button>
                </div>

                <label style={{ fontSize: '.72rem' }}>Channel</label>
                <input
                  type="number"
                  value={channel}
                  onChange={e => setChannel(e.target.value)}
                  min="1" max="64"
                />
              </>
            )}
          </>
        )}

        {error && <div style={{ color: 'var(--red)', fontSize: '.78rem', marginTop: 6 }}>⚠ {error}</div>}
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose} disabled={saving}>Batal</button>
          <button className="btn btn-primary" onClick={submit} disabled={loading || saving}>
            {saving ? 'Menyimpan...' : 'Simpan'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function CCTVPlayer({ src, name, onRemove, removable, camId, onRename, dragHandleProps }) {
  const videoRef = useRef(null)
  const hlsRef   = useRef(null)
  const retryRef = useRef(null)
  const deadRef  = useRef(false)
  const recoverRef = useRef(0)
  const retryCountRef = useRef(0)
  const lastFrameRef = useRef(null)   // data URL of last captured frame
  const [hasPoster, setHasPoster] = useState(false)
  const [status, setStatus] = useState('loading')
  const [muted, setMuted]   = useState(true)
  const [errorMsg, setErrorMsg] = useState(null)
  const [retryKey, setRetryKey] = useState(0)
  const [showPtz,  setShowPtz]  = useState(false)
  const [showEdit, setShowEdit] = useState(false)
  const [ptzCap,   setPtzCap]   = useState(null)  // null=unknown, true, false

  useEffect(() => {
    if (!camId) return
    ptzCheck(camId).then(r => setPtzCap(r.supported)).catch(() => setPtzCap(false))
  }, [camId])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !src) return
    const effectiveSrc = normalizeStreamUrl(src)

    deadRef.current = false
    recoverRef.current = 0
    retryCountRef.current = 0

    function cleanup() {
      clearTimeout(retryRef.current)
      if (capInterval) { clearInterval(capInterval); capInterval = null }
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

      let capInterval = null
      const captureFrame = () => {
        if (!video || video.readyState < 2 || video.videoWidth === 0) return
        try {
          const c = document.createElement('canvas')
          c.width = video.videoWidth; c.height = video.videoHeight
          c.getContext('2d').drawImage(video, 0, 0)
          lastFrameRef.current = c.toDataURL('image/jpeg', 0.5)
          setHasPoster(true)
        } catch { /* tainted canvas — ignore */ }
      }

      const markLive = () => {
        if (deadRef.current) return
        recoverRef.current = 0
        captureFrame()
        if (!capInterval) capInterval = setInterval(captureFrame, 8000)
        setStatus('live')
      }

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
            // Stream gap during ffmpeg restart (TLS session expiry ~90s on Dahua).
            // Retry quickly so viewer sees brief black, not dead state.
            if (retryCountRef.current >= MAX_RETRIES) {
              attachDead('Stream tidak tersedia setelah beberapa percobaan (404)')
              return
            }
            retryCountRef.current += 1
            cleanup()
            setStatus('loading')
            retryRef.current = setTimeout(attach, 2000)   // 2s — stream back in ~1s
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

  return (
    <div className="cam-card">
      <div className="cam-header">
        {dragHandleProps && (
          <span className="cam-drag-handle" {...dragHandleProps} title="Seret untuk pindahkan">⠿</span>
        )}
        <div className="cam-title">
          <span className={`cam-status ${status}`} />
          {name}
        </div>
        <div className="cam-actions">
          <button className="btn-icon" onClick={() => setMuted(m => !m)} title={muted ? 'Unmute' : 'Mute'}>
            {muted ? '🔇' : '🔊'}
          </button>
          {camId && (
            <button
              className="btn-icon"
              onClick={() => ptzCap !== false && setShowPtz(v => !v)}
              title={ptzCap === false ? 'PTZ tidak didukung' : ptzCap === null ? 'Memeriksa PTZ...' : 'PTZ Control'}
              style={{
                color: showPtz ? 'var(--accent)' : undefined,
                opacity: ptzCap === false ? 0.35 : 1,
                cursor: ptzCap === false ? 'not-allowed' : 'pointer',
              }}
            >🎮</button>
          )}
          <button className="btn-icon" onClick={toggleFullscreen} title="Fullscreen">⛶</button>
          <button className="btn-icon" onClick={() => setShowEdit(true)} title="Edit kamera">✏️</button>
          {removable && (
            <button className="btn-icon" onClick={onRemove} title="Hapus channel" style={{color:'var(--red)'}}>✕</button>
          )}
        </div>
      </div>
      <div className={`cam-video ${status === 'loading' && hasPoster ? 'is-reconnecting' : ''}`}>
        {/* Frozen last-frame poster shown while reconnecting */}
        {hasPoster && lastFrameRef.current && (status === 'loading' || status === 'dead') && (
          <img
            src={lastFrameRef.current}
            className="cam-last-frame"
            alt=""
            aria-hidden="true"
          />
        )}
        <video
          ref={(el) => {
            videoRef.current = el
            if (el && !hlsRef.current) {
              el.muted = true
              el.setAttribute('muted', '')
            }
          }}
          autoPlay
          playsInline
        />
        {status === 'loading' && (
          <div className="cam-overlay"><span className="cam-spinner" /></div>
        )}
        {showPtz && camId && <PTZPanel camId={camId} capable={ptzCap} />}
        {status === 'error' && (
          <div className="cam-error">
            <span className="cam-error-icon">📷</span>
            <span>Browser tidak mendukung HLS</span>
          </div>
        )}
        {status === 'dead' && (
          <div className="cam-error" style={{ background: hasPoster ? 'rgba(0,0,0,0.55)' : undefined }}>
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
                setRetryKey(k => k + 1)
              }}
            >↺ Coba lagi</button>
          </div>
        )}
      </div>
      {showEdit && (
        <EditCameraModal
          camId={camId}
          initialName={name}
          onSave={(newName) => {
            if (onRename) onRename(camId, newName)
          }}
          onClose={() => setShowEdit(false)}
        />
      )}
    </div>
  )
}
