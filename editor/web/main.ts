const link = document.querySelector<HTMLAnchorElement>("#open");
if (link) {
  const api = new URLSearchParams(location.search).get("api") ?? "http://127.0.0.1:8000/";
  link.href = api;
}
