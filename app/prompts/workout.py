# Antrenman planı promptları. Şablonlar zaten bağımsız yaşıyor:
# app/services/training_generation/prompt_builder.py + training_assets/
# (few_shots/*.txt, rules/*.json). Bu modül prompt katmanının tek girişi olarak
# oradaki kurucuları yeniden dışa aktarır — şablonların kendisi taşınmaz
# (training_generation testleri ve asset yolları oradaki konuma bağlı).
from app.services.training_generation.prompt_builder import (  # noqa: F401
    build_system_prompt,
    build_training_prompt,
)
