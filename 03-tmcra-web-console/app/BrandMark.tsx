import Image from "next/image";

export default function BrandMark({ className = "" }: { className?: string }) {
  return (
    <span className={`tmcra-mark brand-mark ${className}`.trim()} aria-hidden="true">
      <Image src="/brand/tmcra-mark.png" alt="" width={64} height={64} priority unoptimized />
    </span>
  );
}
