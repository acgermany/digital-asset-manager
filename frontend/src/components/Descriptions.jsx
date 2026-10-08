import { api } from '../api'
import BatchPanel from './BatchPanel'

const SPEC = {
  title: 'Descriptions',
  sub: s => <>Photo captions and video summaries by <code>{s.model}</code> on your Mac</>,
  state: s => !s.reachable ? { tone: 'bad', label: 'Mac offline' }
    : !s.model_installed ? { tone: 'bad', label: 'Model not installed' }
    : s.model_loaded ? { tone: 'ok', label: 'Model loaded' } : { tone: 'idle', label: 'Model not loaded' },
  status: api.describeStatus,
  start: api.startDescribe,
  cancel: api.cancelDescribe,
  doneKey: 'described',
  noun: 'images and videos',
  doneWord: 'described',
  startLabel: 'Describe',
  redoLabel: 'Re-describe all',
  redoHelp: 'Redo every description (after changing the model or prompts)',
  progress: bp => `${bp.described} described`,
  help: 'Precious items go first. Roughly 5–15 seconds per photo and 20–40 seconds per video once the model is loaded. New descriptions are re-embedded so search and the map pick them up.',
}

export default function Descriptions({ onConfigure }) {
  return <BatchPanel spec={SPEC} onConfigure={onConfigure} />
}
