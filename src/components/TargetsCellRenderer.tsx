import { PLATFORM_OPTIONS } from '../config/fieldConfig';

type Params = {
  value?: string[];
  data?: { id?: string };
  context?: {
    updateRowTargets?: (rowId: string, targets: string[]) => void;
    platformOptions?: string[];
  };
};

export default function TargetsCellRenderer(props: Params) {
  const selected = props.value ?? [];
  const rowId = props.data?.id;

  const togglePlatform = (platform: string) => {
    if (!rowId || !props.context?.updateRowTargets) return;

    const nextTargets = selected.includes(platform)
      ? selected.filter((item) => item !== platform)
      : [...selected, platform];

    props.context.updateRowTargets(rowId, nextTargets);
  };

  const options =
    props.context?.platformOptions?.length ? props.context.platformOptions : [...PLATFORM_OPTIONS];

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '6px 0' }}>
      {options.map((platform) => {
        const active = selected.includes(platform);

        return (
          <button
            key={platform}
            type="button"
            onClick={() => togglePlatform(platform)}
            style={{
              border: '1px solid',
              borderColor: active ? '#2563eb' : '#d1d5db',
              background: active ? '#dbeafe' : '#fff',
              color: active ? '#1d4ed8' : '#374151',
              borderRadius: 999,
              padding: '2px 8px',
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            {platform}
          </button>
        );
      })}
    </div>
  );
}

