const chunks=["confinex-app.chunk-01.txt","confinex-app.chunk-02.txt","confinex-app.chunk-03.txt","confinex-app.chunk-04.txt","confinex-app.chunk-05.txt","confinex-app.chunk-06.txt","confinex-app.chunk-07.txt"];
const code=await Promise.all(chunks.map(async name=>{
  const response=await fetch(new URL(name,import.meta.url));
  if(!response.ok) throw new Error("Nao foi possivel carregar "+name);
  return response.text();
}));
const blob=new Blob([code.join("")],{type:"text/javascript"});
await import(URL.createObjectURL(blob));
