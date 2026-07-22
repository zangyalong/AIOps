<script setup lang="ts">
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import { computed } from 'vue'

const props = defineProps<{
  content: string
}>()

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

const defaultLinkOpen =
  markdown.renderer.rules.link_open ??
  ((tokens, index, options, _env, self) => self.renderToken(tokens, index, options))

markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, index, options, env, self)
}

const rendered = computed(() =>
  DOMPurify.sanitize(markdown.render(props.content), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style'],
    FORBID_ATTR: ['style'],
  }),
)
</script>

<template>
  <!-- markdown-it 禁止原始 HTML，DOMPurify 再做一层防御后才允许写入 DOM。 -->
  <div class="markdown-content" v-html="rendered"></div>
</template>

<style scoped>
.markdown-content {
  min-width: 0;
  max-width: 100%;
  overflow-x: clip;
  overflow-wrap: anywhere;
  line-height: 1.65;
}

.markdown-content :deep(> :first-child) {
  margin-top: 0;
}

.markdown-content :deep(> :last-child) {
  margin-bottom: 0;
}

.markdown-content :deep(p),
.markdown-content :deep(ul),
.markdown-content :deep(ol),
.markdown-content :deep(blockquote),
.markdown-content :deep(pre),
.markdown-content :deep(table) {
  margin: 0.55rem 0;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4) {
  margin: 0.85rem 0 0.35rem;
  line-height: 1.35;
}

.markdown-content :deep(h1) {
  font-size: 1.25rem;
}

.markdown-content :deep(h2) {
  font-size: 1.12rem;
}

.markdown-content :deep(h3),
.markdown-content :deep(h4) {
  font-size: 1rem;
}

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  padding-left: 1.35rem;
}

.markdown-content :deep(a) {
  color: var(--color-accent);
  overflow-wrap: anywhere;
  word-break: break-word;
}

.markdown-content :deep(code) {
  padding: 0.08rem 0.3rem;
  border-radius: 4px;
  background: var(--color-bg);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9em;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.markdown-content :deep(pre) {
  max-width: 100%;
  padding: 0.7rem 0.8rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg);
  white-space: pre-wrap;
  overflow: hidden;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.markdown-content :deep(pre code) {
  padding: 0;
  background: transparent;
  white-space: inherit;
}

.markdown-content :deep(blockquote) {
  padding-left: 0.75rem;
  border-left: 3px solid var(--color-border);
  color: var(--color-text-muted);
}

.markdown-content :deep(table) {
  display: table;
  width: 100%;
  max-width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  padding: 0.4rem 0.5rem;
  border: 1px solid var(--color-border);
  overflow-wrap: anywhere;
  word-break: break-word;
  vertical-align: top;
}

.markdown-content :deep(img) {
  display: block;
  max-width: 100%;
  height: auto;
}

.markdown-content :deep(hr) {
  border: 0;
  border-top: 1px solid var(--color-border);
}
</style>
