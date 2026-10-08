import { api } from '../api'
import BatchPanel from './BatchPanel'

const SPEC = {
  title: 'Transcripts',
  sub: s => <>Speech-to-text with Whisper ({s.api === 'openai' ? 'OpenAI-compatible server' : 'whisper.cpp'}) at <code>{s.url}</code></>,
  state: s => s.reachable ? { tone: 'ok', label: 'Server reachable' } : { tone: 'bad', label: 'Server offline' },
  status: api.transcribeStatus,
  start: api.startTranscribe,
  cancel: api.cancelTranscribe,
  doneKey: 'transcribed',
  noun: 'videos and recordings',
  doneWord: 'transcribed',
  extra: cov => cov.no_speech ? ` (${cov.no_speech} with no speech)` : '',
  startLabel: 'Transcribe',
  redoLabel: 'Re-transcribe all',
  redoHelp: 'Redo every transcript (after changing the server, model or language)',
  progress: bp => `${bp.transcribed || 0} transcribed${bp.no_speech ? ` · ${bp.no_speech} no speech` : ''}${bp.unreadable ? ` · ${bp.unreadable} unreadable` : ''}`,
  help: 'Precious items go first. With large-v3-turbo on the M5 Pro expect well under a minute per 10 minutes of audio. Transcripts are searchable, re-embedded, and given to the video summarizer.',
}

export default function Transcripts({ onConfigure }) {
  return <BatchPanel spec={SPEC} onConfigure={onConfigure} />
}
