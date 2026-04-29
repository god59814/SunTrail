from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any
from urllib.parse import quote


@dataclass(frozen=True)
class LocalImageHosting:
    assets_dir: Path
    base_url: str  # e.g. "https://rug-tabs-instruments-thoroughly.trycloudflare.com"

    def to_vendor_url(self, local_path: str | Path) -> str:
        p = Path(local_path)
        assets_root = self.assets_dir.resolve()

        if not p.is_absolute():
            # 若路徑已含 assets 目錄名（例如 assets/商品名/common/desc/desc_01.jpg），
            # 先去掉開頭的 assets，再對 assets_root 解析，這樣產生的 URL 才不會多一層 /assets/。
            # 因為 start_image_hosting 的 http.server 的 cwd 就是 assets，對外根路徑即為 /商品名/...
            parts = p.parts
            if parts and parts[0] == self.assets_dir.name:
                p = (assets_root / Path(*parts[1:])).resolve()
            else:
                p = (assets_root / p).resolve()

        try:
            rel = p.relative_to(assets_root)
        except ValueError as e:
            raise ValueError(f"image path must be under assets_dir: {assets_root} (got {p})") from e

        # URL path must be URL-encoded
        rel_posix = rel.as_posix()
        return self.base_url.rstrip("/") + "/" + quote(rel_posix)

    def build_images_payload(
        self,
        main_image: str | Path,
        detail_images: List[str | Path] | None = None,
    ) -> List[Dict[str, Any]]:
        detail_images = detail_images or []
        urls = [self.to_vendor_url(main_image)] + [self.to_vendor_url(x) for x in detail_images]

        images: List[Dict[str, Any]] = []
        for i, url in enumerate(urls):
            images.append(
                {
                    "imageOrder": i,
                    "imageType": "REPRESENTATION" if i == 0 else "DETAIL",
                    "vendorPath": url,
                }
            )
        return images