import { useState, type ComponentPropsWithoutRef } from 'react';
import { Upload } from 'lucide-react';

type Props = Pick<ComponentPropsWithoutRef<'input'>, 'name' | 'accept' | 'required' | 'disabled' | 'onChange'> & {
  buttonText: string;
};

export function FilePicker({ buttonText, onChange, ...props }: Props) {
  const [filename, setFilename] = useState('');
  return <span className="file-picker">
    <span className="file-trigger">
      <input {...props} type="file" onChange={event => {
        onChange?.(event);
        setFilename(event.currentTarget.files?.[0]?.name ?? '');
      }} />
      <span className="btn secondary" aria-hidden="true"><Upload size={16} />{buttonText}</span>
    </span>
    {filename && <span className="file-name" aria-hidden="true">{filename}</span>}
  </span>;
}
