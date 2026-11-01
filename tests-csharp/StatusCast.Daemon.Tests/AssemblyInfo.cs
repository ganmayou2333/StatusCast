using Xunit;

// The suite shares process-wide state (STATUSCAST_ROOT, HttpListener ports and
// the project-root walk), so classes must not run in parallel.
[assembly: CollectionBehavior(DisableTestParallelization = true)]
