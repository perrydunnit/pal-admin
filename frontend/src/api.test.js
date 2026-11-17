import { describe, expect, it } from 'vitest'
import { apiUrl } from './api.js'

// A phone that opens https://admin:password@host/ has credentials in its base URL.
// Relative /api/... URLs then inherit them, and fetch() throws
// "Request cannot be constructed from a URL that includes credentials".
// Every request must therefore be built from an origin, which never has them.
describe('apiUrl', () => {
  it('builds an absolute URL from the origin and the path', () => {
    expect(apiUrl('/api/timeline?month=2026-11', 'https://pal-admin.example.com')).toBe(
      'https://pal-admin.example.com/api/timeline?month=2026-11',
    )
    expect(apiUrl('/api/events', 'http://192.168.1.10')).toBe('http://192.168.1.10/api/events')
  })

  it('never carries credentials, even when the page URL did', () => {
    const documentUrl = new URL('https://admin:password@pal-admin.example.com/')
    expect(documentUrl.username).toBe('admin')
    // location.origin is the credential-free part, and that is what we use.
    expect(apiUrl('/api/summary', documentUrl.origin)).toBe(
      'https://pal-admin.example.com/api/summary',
    )
  })

  it('keeps query strings and nested paths intact', () => {
    expect(apiUrl('/api/music/abc/file', 'https://h')).toBe('https://h/api/music/abc/file')
  })
})
