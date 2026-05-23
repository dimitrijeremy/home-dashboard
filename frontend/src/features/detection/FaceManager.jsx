import React, { useEffect, useRef, useState } from 'react'
import { fetchFaces, addFace, deleteFace, facePhotoUrl } from '../../services/api'

export default function FaceManager() {
  const [faces, setFaces]     = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [name, setName]       = useState('')
  const [photoFile, setPhotoFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [saving, setSaving]   = useState(false)
  const [error, setError]     = useState(null)
  const fileRef               = useRef(null)

  const reload = () => {
    setLoading(true)
    fetchFaces()
      .then(setFaces)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { reload() }, [])

  const handleFileChange = (e) => {
    const f = e.target.files?.[0]
    if (!f) return
    setPhotoFile(f)
    const reader = new FileReader()
    reader.onload = ev => setPreview(ev.target.result)
    reader.readAsDataURL(f)
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!name.trim()) { setError('Nama wajib diisi'); return }
    if (!photoFile)   { setError('Foto wajib diunggah'); return }
    setSaving(true)
    setError(null)
    try {
      await addFace(name.trim(), photoFile)
      setName(''); setPhotoFile(null); setPreview(null)
      setShowForm(false)
      reload()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id, faceName) => {
    if (!confirm(`Hapus wajah "${faceName}"?`)) return
    await deleteFace(id).catch(() => {})
    setFaces(prev => prev.filter(f => f.id !== id))
  }

  return (
    <div className="face-manager">
      <div className="face-mgr-header">
        <span className="face-mgr-count">{faces.length} wajah terdaftar</span>
        {!showForm && (
          <button className="btn btn-primary" onClick={() => { setShowForm(true); setError(null) }}>
            ＋ Daftarkan Wajah
          </button>
        )}
      </div>

      {error && (
        <div className="face-error">{error}</div>
      )}

      {/* Enroll form */}
      {showForm && (
        <form className="face-enroll-form" onSubmit={handleSubmit}>
          <div className="face-enroll-row">
            <div
              className="face-photo-drop"
              onClick={() => fileRef.current?.click()}
              title="Klik untuk pilih foto"
            >
              {preview ? (
                <img src={preview} alt="preview" className="face-preview-img" />
              ) : (
                <span className="face-photo-placeholder">📷<br/><small>Pilih foto</small></span>
              )}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              style={{ display: 'none' }}
              onChange={handleFileChange}
            />
            <div className="face-enroll-fields">
              <label className="face-enroll-label">Nama</label>
              <input
                className="face-enroll-input"
                placeholder="Contoh: Pak Budi"
                value={name}
                onChange={e => setName(e.target.value)}
                autoFocus
              />
              <small className="face-enroll-hint">
                Gunakan foto wajah yang jelas, pencahayaan baik, tampak depan.
              </small>
            </div>
          </div>
          <div className="face-enroll-actions">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Menyimpan…' : '💾 Simpan'}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => { setShowForm(false); setName(''); setPhotoFile(null); setPreview(null); setError(null) }}
            >
              Batal
            </button>
          </div>
        </form>
      )}

      {/* Face grid */}
      {loading ? (
        <div className="face-loading">Memuat…</div>
      ) : faces.length === 0 ? (
        <div className="face-empty">
          Belum ada wajah terdaftar. Klik "Daftarkan Wajah" untuk menambahkan.
        </div>
      ) : (
        <div className="face-grid">
          {faces.map(face => (
            <div key={face.id} className="face-card">
              <div className="face-card-photo">
                <img
                  src={facePhotoUrl(face.id)}
                  alt={face.name}
                  onError={e => { e.target.style.display = 'none' }}
                />
              </div>
              <div className="face-card-name">{face.name}</div>
              <div className="face-card-date">
                {new Date(face.created_at + 'Z').toLocaleDateString('id-ID')}
              </div>
              <button
                className="face-card-del btn-icon"
                title="Hapus"
                onClick={() => handleDelete(face.id, face.name)}
              >
                🗑
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
