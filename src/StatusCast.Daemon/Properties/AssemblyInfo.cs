using System.Runtime.CompilerServices;

// The daemon's parsing/serialization helpers are internal on purpose (they are
// implementation details of a console app, not a public API). The test project
// lives in the same repo and needs to exercise them directly.
[assembly: InternalsVisibleTo("StatusCast.Daemon.Tests")]
