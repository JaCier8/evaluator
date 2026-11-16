// Fake environment: follows the evaluator protocol, but its only "simulation"
// is spending a configured amount of time per step. See fake_common.hpp for knobs.
// argv: <test_name> [key=value ...]
#include "fake_common.hpp"

int main(int argc, char* argv[])
{
    if (argc < 2) return 1;
    Knobs k = parse_knobs(argc, argv);

    char state[STATE_SIZE + 1];
    char action[ACTION_SIZE + 1];
    std::memset(state, 'x', STATE_SIZE);
    state[STATE_SIZE] = '\n';

    work(k.env_init_ms, k.busy);
    for (long step = 0;; step++) {
        state[0] = (step < k.steps) ? 'A' : 'T'; // 'T' = terminal
        if (!write_n(STDOUT_FILENO, state, sizeof state)) return 1;
        if (state[0] == 'T') return 0;
        if (!read_n(STDIN_FILENO, action, sizeof action)) return 1;
        work(k.env_step_ms, k.busy);
    }
}
