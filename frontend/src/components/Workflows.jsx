import Ingest from './Ingest'
import Review from './Review'
import Duplicates from './Duplicates'
import ScanControl from './ScanControl'

const WORKFLOWS = ['Ingest', 'Review', 'Duplicates', 'Scan']

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
    </>
  )
}
