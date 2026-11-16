// Fake policy: answers every state with a fixed action after spending a
// configured amount of time. See fake_common.hpp for knobs.
// argv: <policy_id> [key=value ...]
#include "fake_common.hpp"

int main(int argc, char* argv[])
{
    if (argc < 2) return 1;
    Knobs k = parse_knobs(argc, argv);

    char state[STATE_SIZE + 1];
    char action[ACTION_SIZE + 1];
    std::memset(action, 'a', ACTION_SIZE);
    action[ACTION_SIZE] = '\n';

    work(k.pol_init_ms, k.busy); // e.g. loading model weights
    while (read_n(STDIN_FILENO, state, sizeof state)) {
        work(k.pol_step_ms, k.busy);
        if (!write_n(STDOUT_FILENO, action, sizeof action)) return 1;
    }
    return 0;
}
