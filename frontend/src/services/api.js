const BACKEND = import.meta.env.VITE_BACKEND_URL || 'http://backend:5000'

export async function fetchCameras(){
  const res = await fetch(`${BACKEND}/api/cameras`)
  if(!res.ok) throw new Error('Failed to fetch')
  return res.json()
}
