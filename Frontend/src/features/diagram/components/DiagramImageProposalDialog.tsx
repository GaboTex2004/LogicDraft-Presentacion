import { partitionImageWarnings, type ImageProposal } from '../services/imageProposal'

export function DiagramImageProposalDialog({ proposal, busy, error, onAccept, onCancel }: {
  proposal: ImageProposal | null
  busy: boolean
  error: string
  onAccept: () => void
  onCancel: () => void
}) {
  if (!proposal) return null
  const { pendingRelationships, informationalWarnings } = partitionImageWarnings(proposal.warnings)
  const sections: [string, string[]][] = [
    ['Elementos existentes encontrados', proposal.matched],
    ['Elementos válidos que se añadirán', proposal.additions],
    ['Relaciones incompletas que quedarán pendientes', pendingRelationships],
    ['Conflictos (impiden confirmar)', proposal.conflicts],
    ['Advertencias informativas', informationalWarnings],
  ]
  return <div className="association-dialog-backdrop" role="presentation">
    <section className="association-dialog" role="dialog" aria-modal="true" aria-labelledby="image-proposal-title">
      <h2 id="image-proposal-title">Actualizacion aditiva por imagen</h2>
      <p>Revision base: {proposal.baseRevision ?? 'sin documento guardado'}. Solo se incorporaran altas. Los elementos ausentes en la imagen se conservan.</p>
      <p>La propuesta expira a los 10 minutos. Si cambia el diagrama, vuelve a analizar la imagen.</p>
      <div style={{ maxHeight: '55vh', overflowY: 'auto' }}>
        {sections.map(([title, items]) => <section key={title}>
          <h3>{title} ({items.length})</h3>
          {items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>Ninguno.</p>}
        </section>)}
      </div>
      {error && <p role="alert">{error}</p>}
      <div className="association-dialog-actions">
        <button type="button" disabled={busy} onClick={onCancel}>Cancelar</button>
        <button type="button" disabled={busy || !proposal.canApply} onClick={onAccept}>
          {busy ? 'Guardando...' : 'Confirmar altas y guardar'}
        </button>
      </div>
    </section>
  </div>
}
