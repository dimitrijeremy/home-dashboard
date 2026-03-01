import React, {useEffect, useState} from 'react'
import {fetchCameras} from '../services/api'
import CCTVPlayer from '../features/cctv/CCTVPlayer'

export default function Dashboard(){
  const [cams, setCams] = useState([])
  const [error, setError] = useState(null)

  useEffect(()=>{
    fetchCameras().then(setCams).catch(e=>setError(e.message))
  },[])

  return (
    <div className="container">
      <h1 style={{marginBottom:12}}>Home Dashboard</h1>
      {error && <div style={{color:'salmon'}}>{error}</div>}
      <div className="grid">
        {cams.map(cam=> (
          <CCTVPlayer key={cam.id} src={cam.stream_url} name={cam.name} />
        ))}
      </div>
    </div>
  )
}
