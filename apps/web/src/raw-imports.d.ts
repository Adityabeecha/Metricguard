// Vite's ?raw imports (used by the /guide page) give the file's text.
declare module '*.md?raw' {
  const text: string;
  export default text;
}
