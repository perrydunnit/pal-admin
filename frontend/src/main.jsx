import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import { forgetUrlCredentials } from './api.js'
import './styles.css'

// Before anything else: a page opened as https://user:pass@host/ keeps those
// credentials in its base URL, which breaks every relative fetch(). Requests are
// built from location.origin anyway; this also cleans the address bar.
forgetUrlCredentials()

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
