import React, { useEffect, useRef, useState } from 'react'
import Hls from 'hls.js'

function normalizeStreamUrl(src) {
  try {
    const base = typeof window !== 'undefined' ? window.location.href : 'http://localhost'
    const url = new URL(src, base)

    // mediamtx playlists expose separate audio/video tracks.
    // For the dashboard we prefer the video-only playlist because audio is not
    // required and the single-track path avoids extra SourceBuffer churn.
    if (/^\/(?:ch\d+|custom_[0-9a-f]+)\/index\.m3u8$/.test(url.pathname)) {
      url.pathname = url.pathname.replace(/\/index\.m3u8$/, '/video1_stream.m3u8')
      return url.toString()
    }
  } catch {
    // Leave custom or invalid URLs untouched.
  }

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

export default function CCTVPlayer({ src, name, onRemove, removable }) {
  const videoRef = useRef(null)
  const hlsRef   = useRef(null)
  const retryRef = useRef(null)
  const deadRef  = useRef(false)
  const recoverRef = useRef(0)
  const [status, setStatus] = useState('loading')
  const [muted, setMuted]   = useState(true)

  useEffect(() => {
    const video = videoRef.current
    if (!video || !src) return
    const effectiveSrc = normalizeStreamUrl(src)

    deadRef.current = false
    recoverRef.current = 0

    function cleanup() {
      clearTimeout(retryRef.current)
      video.onloadeddata = null
      video.onplaying = null
      if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null }
    }

    function attach() {
      if (deadRef.current) return
      cleanup()
      setStatus('loading')

      // Ensure muted+autoplay attributes are set before any load.
      // React's muted JSX prop does NOT reflect to the DOM attribute,
      // so we set both the property and the attribute here.
      video.muted = true
      video.setAttribute('muted', '')

      const markLive = () => {
        if (deadRef.current) return
        recoverRef.current = 0
        setStatus('live')
        video.play().catch(() => {})
      }

      video.onloadeddata = markLive
      video.onplaying = markLive

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
          video.play().catch(() => {})
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
          cleanup()
          setStatus('loading')
          retryRef.current = setTimeout(attach, 6000)
        })
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        // Safari native HLS — autoPlay + muted handles play() automatically
        video.src = effectiveSrc
        video.load()
        video.addEventListener('canplay', () => { if (!deadRef.current) markLive() }, { once: true })
        video.addEventListener('error',   () => { if (!deadRef.current) { retryRef.current = setTimeout(attach, 4000) } }, { once: true })
      } else {
        setStatus('error')
      }
    }

    attach()
    return () => { deadRef.current = true; cleanup() }
  }, [src])

  useEffect(() => {
    if (videoRef.current) videoRef.current.muted = muted
  }, [muted])

  const toggleFullscreen = () => {
    const el = videoRef.current
    if (!el) return
    document.fullscreenElement ? document.exitFullscreen() : el.requestFullscreen?.()
  }

  return (
    <div className="cam-card">
      <div className="cam-header">
        <div className="cam-title">
          <span className={`cam-status ${status}`} />
          {name}
        </div>
        <div className="cam-actions">
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
            if (el) {
              el.muted = true              // DOM property
              el.setAttribute('muted', '') // DOM attribute — required for autoplay policy
            }
          }}
          autoPlay
          playsInline
        />
        {status === 'loading' && (
          <div className="cam-overlay"><span className="cam-spinner" /></div>
        )}
        {status === 'error' && (
          <div className="cam-error">
            <span className="cam-error-icon">📷</span>
            <span>Stream tidak tersedia</span>
          </div>
        )}
      </div>
    </div>
  )
}
