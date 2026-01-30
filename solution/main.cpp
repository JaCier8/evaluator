
// common/err.h
#ifdef __cplusplus
#define _Noreturn [[noreturn]]
#endif
#include <stdatomic.h>

extern "C" {
#include "common/err.h"
}

#include <asm-generic/errno-base.h>
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sys/wait.h>
#include <unistd.h>
#include <fcntl.h>
#include <semaphore.h>
#include <stdatomic.h>
#include <sys/mman.h>
#include <vector>
#include <string>
#include <map>


char *policy_path;
char *env_path;
char **extra_args;

int max_concurrent_policy_calls;
int max_concurrent_calls;
int max_active_envs;
int fd_to_writer;

struct Global {
    sem_t sem_env;
    sem_t sem_cpu;
    sem_t sem_pol;

    atomic_int next_ticket; // TODO maybe another atomic
    atomic_int next_policy_id;
    atomic_bool shutdown;
} Global;

struct Result {
    int test_num;
    char test_name[NAME_SIZE + 1];
    char test_result[STATE_SIZE + 1];
};

struct Global* global = nullptr;

void shm_init();
void shm_cleanup();
int worker(int my_ticket, const char* test_name);
pid_t start_subprocess(const char* path, char* const argv[],
    int* fd_write_to_child, int* fd_read_from_child);
std::vector<char*> extra_args_to_argv (const char* exec_path,
    const char* program_arg);
int write_exactly(int fd, const char* buff, size_t size);
int read_exactly(int fd, char* buff, size_t size);
int safe_sem_wait(sem_t* sem);
void cleanup_processes(pid_t env_pid, pid_t pol_pid);
pid_t result_printer();

void handle_sigint(int) {
    if (global) {
        atomic_store(&global->shutdown, true);
    }
}

int main(int argc, char *argv[]){
    assert(argc >= 6);

    policy_path = argv[1];
    env_path = argv[2];
    extra_args = &argv[6];

    max_concurrent_policy_calls = atoi(argv[3]);
    max_concurrent_calls = atoi(argv[4]);
    max_active_envs = atoi(argv[5]);

    struct sigaction act;
    sigemptyset(&act.sa_mask);
    act.sa_flags = 0;
    act.sa_handler = handle_sigint;


    if (sigaction(SIGINT, &act, NULL) == -1) {
        perror("sigaction");
        return 1;
    }


    shm_init();

    int local_ticket_counter = 0;

    // Reading tests from stdin
    char test_name[NAME_SIZE + 2];

    pid_t printer_pid = result_printer();

    while (true) {

        // Killing zombie childs
        while (waitpid(-1, nullptr, WNOHANG) > 0) {}

        // Using fgets, for easier ctr+c handling
        if (fgets(test_name, sizeof(test_name), stdin) == NULL) {
            if (errno == EINTR || atomic_load(&global->shutdown)) {
                break; // SIGINT
            }
            break; // EOF
        }

        // Deleting new_line sign
        size_t len = strlen(test_name);
        if (len > 0 && test_name[len - 1] == '\n') {
            test_name[len - 1] = '\0';
            len--; // Napis jest teraz krótszy
        }
        assert(len == NAME_SIZE);

        // Whenever we have a test and free place for env,
        // we try to make a new worker
        if (safe_sem_wait(&global->sem_env) != 0) break;

        int my_ticket = local_ticket_counter++;

        pid_t pid = fork();
        ASSERT_SYS_OK(pid);

        if (pid == 0) {
            worker(my_ticket, test_name);
            exit(0);
        }
    }

    close(fd_to_writer);
    while (waitpid(-1, nullptr, WNOHANG) > 0) {
        // TODO o co chodzi
    }
    bool was_interrupted = false;
    if (global) {
        was_interrupted = atomic_load(&global->shutdown);
    }

    waitpid(printer_pid, NULL, 0);

    if (was_interrupted) {
        return 2;
    }

    return 0;
}

void safe_close_pipes(int fd1, int fd2, int fd3, int fd4) {
    if (fd1 != -1) close(fd1);
    if (fd2 != -1) close(fd2);
    if (fd3 != -1) close(fd3);
    if (fd4 != -1) close(fd4);
}

int worker(int test_num, const char* test_name) {

    int env_in = -1, env_out = -1;
    pid_t env_pid = 0;

    int pol_in = -1, pol_out = -1;
    pid_t pol_pid = 0;

    char state_buffer[STATE_SIZE + 2];
    char action_buffer[ACTION_SIZE + 2];

    // Updating policy numbering
    int my_policy_id = atomic_fetch_add(&global->next_policy_id, 1);

    // Fetching and incrementing policy_id.
    std::string pol_id_str = std::to_string(my_policy_id);

    // Building argvs to pol and env.
    std::vector<char*> env_argv = extra_args_to_argv(env_path, test_name);
    std::vector<char*> pol_argv = extra_args_to_argv(policy_path, pol_id_str.c_str());

    // Starting env
    if (safe_sem_wait(&global->sem_cpu) != 0) {
        sem_post(&global->sem_env);
        return 1;
    }
    env_pid = start_subprocess(env_path, env_argv.data(), &env_in, &env_out);

    // Reading starting state from environment.
    if (read_exactly(env_out, state_buffer, STATE_SIZE + 1) != 0) {
        sem_post(&global->sem_cpu);
        safe_close_pipes(env_in, env_out, pol_in, pol_out);
        cleanup_processes(env_pid, pol_pid);
        return 1;
    }

    sem_post(&global->sem_cpu);

    pol_pid = start_subprocess(policy_path, pol_argv.data(), &pol_in, & pol_out);

    // Looping -> tell state to policy -> get action from policy ->
    // tell action to environment -> get state from environment ->
    // check if starts on 'T' repeat
    while (state_buffer[0] != 'T') {
        // Getting gpu for policy
        if (safe_sem_wait(&global->sem_pol) != 0) {
            safe_close_pipes(env_in, env_out, pol_in, pol_out);
            cleanup_processes(env_pid, pol_pid);
            return 1;
        }
        // Getting cpu for policy
        if (safe_sem_wait(&global->sem_cpu) != 0) {
            sem_post(&global->sem_pol);
            safe_close_pipes(env_in, env_out, pol_in, pol_out);
            cleanup_processes(env_pid, pol_pid);
            return 1;
        }

        // Writing our state to ready policy
        if (write_exactly(pol_in, state_buffer, STATE_SIZE + 1) != 0) {
            sem_post(&global->sem_pol);
            sem_post(&global->sem_cpu);
            safe_close_pipes(env_in, env_out, pol_in, pol_out);
            cleanup_processes(env_pid, pol_pid);
            return 1;
        }

        // Getting state from ready policy
        if (read_exactly(pol_out, action_buffer, ACTION_SIZE + 1) != 0) {
            sem_post(&global->sem_pol);
            sem_post(&global->sem_cpu);
            safe_close_pipes(env_in, env_out, pol_in, pol_out);
            cleanup_processes(env_pid, pol_pid);
            return 1;
        }

        // Policy ended
        sem_post(&global->sem_pol);

        // Telling environment action to do
        if (write_exactly(env_in, action_buffer, ACTION_SIZE + 1) != 0) {
            sem_post(&global->sem_cpu);
            safe_close_pipes(env_in, env_out, pol_in, pol_out);
            cleanup_processes(env_pid, pol_pid);
            return 1;
        }

        // Getting a new state from environment
        if (read_exactly(env_out, state_buffer, STATE_SIZE + 1) != 0) {
            sem_post(&global->sem_cpu);
            safe_close_pipes(env_in, env_out, pol_in, pol_out);
            cleanup_processes(env_pid, pol_pid);
            return 1;
        }

        sem_post(&global->sem_cpu);
    }

    state_buffer[STATE_SIZE + 1] = '\0';

    struct Result result;

    strncpy(result.test_name, test_name, NAME_SIZE);
    result.test_name[NAME_SIZE] = '\0';

    strncpy(result.test_result, state_buffer, STATE_SIZE);
    result.test_result[STATE_SIZE] = '\0';

    result.test_num = test_num;

    write_exactly(fd_to_writer, (char*)&result, sizeof(struct Result));

    safe_close_pipes(env_in, env_out, pol_in, pol_out);
    cleanup_processes(env_pid, pol_pid);
    sem_post(&global->sem_env);
    return 0;
}


pid_t result_printer() {

    int next_to_print = 0;
    std::map<int, Result> result_map;

    int fd[2];

    ASSERT_SYS_OK(pipe(fd));

    pid_t pid = fork();

    ASSERT_SYS_OK(pid);

    // Child
    if (pid == 0) {

        close(fd[1]); // Closing writing end

        while (true) {
            struct Result result;
            ssize_t r = read(fd[0], &result, sizeof(Result));
            if (r <= 0) break; // TODO better error handling

            result_map[result.test_num] = result;

            while (result_map.count(next_to_print)) {
                Result to_print = result_map[next_to_print];

                printf("%s %s\n",to_print.test_name, to_print.test_result);
                fflush(stdout);
                result_map.erase(next_to_print);
                next_to_print++;
            }
        }
        exit(0);
    }
    // Parent
    close(fd[0]);
    fd_to_writer = fd[1];
    return pid;
}

// Starting subprocess with initialized pipes,
// returns childs PID
pid_t start_subprocess(const char* path, char* const argv[],
    int* fd_write_to_child, int* fd_read_from_child) {

    int child_stdin_pipe[2]; // Parent -> child
    int child_stdout_pipe[2]; // Child -> parent

    ASSERT_SYS_OK(pipe(child_stdin_pipe));
    ASSERT_SYS_OK(pipe(child_stdout_pipe));

    pid_t pid = fork();
    ASSERT_SYS_OK(pid);

    // Child
    if (!pid) {
        // Closing other ends
        ASSERT_SYS_OK(close(child_stdin_pipe[1]));
        ASSERT_SYS_OK(close(child_stdout_pipe[0]));

        // Duplicating to stdout i stdin for subprocess for communication
        // without interfering with subproc code
        ASSERT_SYS_OK(dup2(child_stdin_pipe[0], STDIN_FILENO));
        ASSERT_SYS_OK(dup2(child_stdout_pipe[1], STDOUT_FILENO));

        // Closing before duped ends
        ASSERT_SYS_OK(close(child_stdin_pipe[0]));
        ASSERT_SYS_OK(close(child_stdout_pipe[1]));

        // Executing subproc
        ASSERT_SYS_OK(execv(path, argv));
    }
    // Parent

    // Closing other ends
    ASSERT_SYS_OK(close(child_stdin_pipe[0]));
    ASSERT_SYS_OK(close(child_stdout_pipe[1]));

    // Giving main process communication descriptors
    *fd_write_to_child = child_stdin_pipe[1];
    *fd_read_from_child = child_stdout_pipe[0];

    return pid;
}

// Shared memmory initialization with all semaphors
void shm_init() {

    size_t shm_size = sizeof(Global);

    void *ptr = mmap(
        nullptr,
        shm_size,
        PROT_READ | PROT_WRITE,
        MAP_SHARED | MAP_ANONYMOUS, // TODO validate ananymous decision
        -1,
        0
        );

    global = static_cast<struct Global*>(ptr);

    sem_init(&global->sem_env, 1, max_active_envs);
    sem_init(&global->sem_cpu, 1, max_concurrent_calls);
    sem_init(&global->sem_pol, 1, max_concurrent_policy_calls);

    atomic_init(&global->next_ticket, 0);
    atomic_init(&global->next_policy_id, 0);
    atomic_init(&global->shutdown, false);
}

// Standard cleaning of shared memmory
void shm_cleanup() {
    if (!global) return; // We don't call it many times
    sem_destroy(&global->sem_env);
    sem_destroy(&global->sem_cpu);
    sem_destroy(&global->sem_pol);

    munmap(global, sizeof(Global));
}

// Helper funtion for safe reading from pipe a message of given size
int read_exactly(int fd, char* buff, size_t size) {
    size_t total_read = 0;

    while (total_read < size) {
        ssize_t r = read(fd, buff + total_read, size - total_read);
        if (r < 0) {
            if (errno == EINTR) { // SIGINT
                if (atomic_load(&global->shutdown)) return -1;
                continue;
            }
        }
        if (r == 0) return -1; // If pipe was closed

        total_read += r;
    }
    return 0;
}

// Helper funtion for safe writing from pipe a message of given size
int write_exactly(int fd, const char* buff, size_t size) {

    size_t total_written = 0;

    while (total_written < size) {
        ssize_t w = write(fd, buff + total_written, size - total_written);

        if (w < 0) {
            if (errno == EINTR) { // SIGINT
                if (atomic_load(&global->shutdown)) return -1;
                continue;
            }
            if (errno == EPIPE) return 1; // Pipe was broken
        }
        if (w == 0) return 1; // Nothing went to pipe, but pipe didn't crash

        total_written += w;
    }
    return 0;
}

// Helper function for building argv list for executable subprocesses
std::vector<char*> extra_args_to_argv (const char* exec_path, const char* program_arg) {
    std::vector<char*> args;

    // Subprocess [path, program_arg]
    args.push_back(const_cast<char*>(exec_path));
    args.push_back(const_cast<char*>(program_arg));

    // Iterring extra arguements
    if (extra_args != nullptr) {
        char** current = extra_args;
        while (*current != nullptr) {
            args.push_back(*current);
            current++;
        }
    }

    args.push_back(nullptr);
    return args;
}

// Functioon to safely hadnling interruptions while waiting for semaphore
int safe_sem_wait(sem_t* sem) {
    while (true) {
        if (sem_wait(sem) == 0) {
            if (atomic_load(&global->shutdown)) {
                sem_post(sem);
                return -1;
            }
            return 0;
        }
        if (errno == EINTR) {
            if (atomic_load(&global->shutdown)) return -1;
            continue;
        }
        return -1;
    }
}

// Interrupting env and policy if interrupted
void cleanup_processes(pid_t env_pid, pid_t pol_pid) {
    if (pol_pid > 0) {
        kill(pol_pid, SIGINT);
        waitpid(pol_pid, NULL, 0);
    }
    if (env_pid > 0) {
        kill(env_pid, SIGINT);
        waitpid(env_pid, NULL, 0);
    }
}







