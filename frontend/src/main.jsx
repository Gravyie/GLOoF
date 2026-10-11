import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { setWorkerUrl } from 'maplibre-gl'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import './index.css'
import App from './App.jsx'

// maplibre v6 loads its worker from a file next to its own module. Production builds
// inline maplibre into the app bundle and never emit that file (404, blank terrain),
// so ship the worker as a build asset and point maplibre at it.
setWorkerUrl(maplibreWorkerUrl)

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
