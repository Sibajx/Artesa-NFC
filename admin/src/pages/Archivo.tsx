import { useSearchParams } from 'react-router-dom';
import { SectionTabs } from '../ui';
import Apartado from './Apartado';

const TABS = [
  { key: 'archivados', label: 'Archivados' },
  { key: 'papelera', label: 'Papelera' },
];

// Archivados and Papelera share one menu entry: what was taken off the site, and
// what was sent to the trash.
export default function Archivo() {
  const [params] = useSearchParams();
  const view = params.get('vista') === 'papelera' ? 'papelera' : 'archivados';
  return (
    <div className="max-w-6xl mx-auto">
      <SectionTabs tabs={TABS} current={view} />
      <Apartado key={view} mode={view} />
    </div>
  );
}
