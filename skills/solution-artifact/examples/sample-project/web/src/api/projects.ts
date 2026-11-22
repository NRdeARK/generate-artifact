// Sample code for the Status Board example. Line numbers here are referenced by usecases.json "call" fields.
export async function loadProjects(): Promise<Project[]> {
  const res = await fetch("/projects");
  if (!res.ok) throw new Error("status load failed");
  return res.json();
}
