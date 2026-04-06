type SignalTone = 'positive' | 'negative' | 'neutral'

export interface StatusSignalItem {
  label: string
  value: string
  tone?: SignalTone
  detail?: string
}

function SignalBadge({
  label,
  value,
  tone = 'neutral',
  detail,
}: StatusSignalItem) {
  const toneClass = tone === 'positive'
    ? 'bg-red-50 border-red-100 text-red-700'
    : tone === 'negative'
      ? 'bg-green-50 border-green-100 text-green-700'
      : 'bg-gray-50 border-gray-100 text-gray-700'

  return (
    <div className={`rounded-lg border px-3 py-2 ${toneClass}`}>
      <div className="text-[11px] opacity-70">{label}</div>
      <div className="text-sm font-semibold">{value}</div>
      {detail && <div className="text-[11px] opacity-70 mt-0.5">{detail}</div>}
    </div>
  )
}

export default function StatusSignalPanel({
  title,
  signals,
}: {
  title: string
  signals: StatusSignalItem[]
}) {
  if (signals.length === 0) return null

  return (
    <div className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">{title}</h2>
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        {signals.map((signal) => (
          <SignalBadge
            key={signal.label}
            label={signal.label}
            value={signal.value}
            tone={signal.tone}
            detail={signal.detail}
          />
        ))}
      </div>
    </div>
  )
}
