import React, {useEffect, useRef} from 'react'
import Hls from 'hls.js'

export default function CCTVPlayer({src, name}){
  const videoRef = useRef(null)

  useEffect(()=>{
    const video = videoRef.current
    if(!video) return

    if(Hls.isSupported()){
      const hls = new Hls()
      hls.loadSource(src)
      hls.attachMedia(video)
      hls.on(Hls.Events.MANIFEST_PARSED, ()=> video.play().catch(()=>{}))
      return ()=>{ hls.destroy() }
    } else if(video.canPlayType('application/vnd.apple.mpegurl')){
      video.src = src
      video.addEventListener('loadedmetadata', ()=> video.play().catch(()=>{}))
    }
  },[src])

  return (
    <div className="card">
      <h3 style={{marginBottom:8}}>{name}</h3>
      <video ref={videoRef} className="video" controls muted playsInline />
    </div>
  )
}
