import { clock } from '../format.js'

export default function ChatPanel({ summary }) {
  const chat = summary?.chat ?? []
  if (!chat.length) return <div className="panel x"><div className="empty">No chat yet.</div></div>

  const rows = chat.slice(-40).reverse()

  return (
    <div className="panel">
      <div className="chat-scroll">
        <table>
          <thead>
            <tr>
              <th className="num">When</th>
              <th>Player</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m, i) => (
              <tr key={`${m.ts}-${m.name}-${i}`}>
                <td className="num muted">{clock(m.ts)}</td>
                <td>{m.name}</td>
                <td style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
