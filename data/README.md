# Local PDF inputs

Place source PDFs here for local runs, for example:

```bash
uv run article-translator --config config/default.toml run data/article.pdf
```

PDFs in this directory are ignored by Git because they may contain private or
copyrighted material. Automated tests generate synthetic PDFs in temporary
directories and must not depend on files stored here.
