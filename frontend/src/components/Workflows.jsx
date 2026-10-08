import Ingest from './Ingest'
import Review from './Review'
import Duplicates from './Duplicates'
import ScanControl from './ScanControl'
import Embeddings from './Embeddings'
import Descriptions from './Descriptions'
import Transcripts from './Transcripts'
import Queues from './Queues'

const WORKFLOWS = ['Ingest', 'Review', 'Duplicates', 'Scan', 'AI', 'Queues']

export default function Workflows({ active, onSelect, onConfigure }) {
  return (
    <>
      <nav className="subnav">
        {WORKFLOWS.map(w => (
          <button
            key={w}
            className={`subnav-btn${active === w ? ' active' : ''}`}
            onClick={() => onSelect(w)}
          >
            {w}
          </button>
        ))}
      </nav>

      {active === 'Ingest'     && <Ingest onConfigure={onConfigure} onReview={() => onSelect('Review')} />}
      {active === 'Review'     && <Review />}
      {active === 'Duplicates' && <Duplicates />}
      {active === 'Scan'       && <ScanControl />}
      {active === 'Queues' && <Queues />}
      {active === 'AI' && <>
        <Descriptions onConfigure={onConfigure} />
        <div style={{ height: 12 }} />
        <Transcripts onConfigure={onConfigure} />
        <div style={{ height: 12 }} />
        <Embeddings onConfigure={onConfigure} />
      </>}
    </>
  )
}
