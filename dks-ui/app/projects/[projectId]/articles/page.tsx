export default function ArticlesIndex() {
  // The user picks an article from the sidebar; this is just the empty state.
  return (
    <div className="flex items-center justify-center h-full text-vault-muted">
      <div className="text-center">
        <p className="text-display text-2xl mb-2 text-vault-text/30">Select an article</p>
        <p className="text-sm">Choose from the sidebar to start reading</p>
      </div>
    </div>
  );
}