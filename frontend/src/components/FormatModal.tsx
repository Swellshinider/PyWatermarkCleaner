import { useEffect, useRef } from "react";
import type { FormatPolicy } from "../api";

interface Props {
  onChoose: (policy: FormatPolicy) => void;
  onClose: () => void;
}

export default function FormatModal({ onChoose, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);

  return (
    <dialog ref={ref} className="modal" aria-labelledby="fmt-title" onClose={onClose}>
      <h2 id="fmt-title">AVI or WebM in this batch</h2>
      <p>
        Convert these videos to MP4 for the widest playback, or keep each one in the format it came
        in.
      </p>
      <div className="modal-actions">
        <button type="button" className="btn btn-primary" autoFocus onClick={() => onChoose("mp4")}>
          Convert to MP4
        </button>
        <button type="button" className="btn" onClick={() => onChoose("original")}>
          Keep original formats
        </button>
        <button type="button" className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
    </dialog>
  );
}
