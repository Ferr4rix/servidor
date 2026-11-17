//go:build windows

// Instalar.exe e Jogar.exe (Windows) — o mesmo código, dois programas:
//
//	Instalar.exe  faz o que fazia "instalar_cliente.bat": arranja o Python 3.12
//	              (baixa e instala só para o utilizador, se não houver), cria a
//	              pasta .venv e instala as bibliotecas de requirements.txt.
//	Painel.exe    (projeto painel_admin) abre o Painel de administração: só
//	              precisa do Python 3.12 (instala se faltar; não usa .venv).
//	Jogar.exe     faz o que fazia "jogar.bat": abre o jogo com o Python da .venv
//	              (sem janela preta). Se ainda não instalou (ou requirements.txt
//	              mudou), chama o Instalar.exe primeiro.
//
// Os dois trabalham na pasta onde está o .exe (não precisa de "cd").
// Compilar (no Linux/Mac/Windows com Go 1.21+):  python tools/compilar_launchers.py
package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

// "instalar" ou "jogar" (definido ao compilar: -ldflags "-X main.mode=jogar")
var mode = "instalar"

const (
	pythonVersion = "3.12.10"
	pythonURL     = "https://www.python.org/ftp/python/" + pythonVersion + "/python-" + pythonVersion + "-amd64.exe"
	stampFile     = ".venv\\instalado.txt"
)

func main() {
	exe, err := os.Executable()
	if err == nil {
		_ = os.Chdir(filepath.Dir(exe))
	}
	if mode == "jogar" {
		play()
		return
	}
	if mode == "painel" {
		panel()
		return
	}
	code := 0
	if err := install(); err != nil {
		fmt.Println()
		fmt.Println("ERRO:", err)
		code = 1
	} else {
		fmt.Println()
		fmt.Println("Pronto! Para jogar, abra o Jogar.exe.")
	}
	if os.Getenv("JOGO_SEM_PAUSA") == "" {
		fmt.Print("\nPrima Enter para fechar...")
		bufio.NewReader(os.Stdin).ReadString('\n')
	}
	os.Exit(code)
}

// ---------------------------------------------------------------- instalar
func install() error {
	if _, err := os.Stat("main.py"); err != nil {
		return fmt.Errorf("o Instalar.exe tem de ficar na pasta do jogo (ao lado do main.py)")
	}
	fmt.Println("== Instalar o jogo ==")
	py, err := findPython()
	if err != nil {
		return err
	}
	if py == nil {
		fmt.Println("Python 3.12 não encontrado: a baixar o instalador oficial (python.org)...")
		if err := downloadPython(); err != nil {
			return err
		}
		if py, err = findPython(); err != nil || py == nil {
			return fmt.Errorf("o Python 3.12 não ficou instalado; instale-o à mão em https://www.python.org/downloads/ e rode de novo")
		}
	}
	fmt.Println("Python:", strings.Join(py, " "))
	venvPy := filepath.Join(".venv", "Scripts", "python.exe")
	if _, err := os.Stat(venvPy); err != nil {
		fmt.Println("A criar a pasta .venv...")
		if err := run(append(py, "-m", "venv", ".venv")...); err != nil {
			return fmt.Errorf("não foi possível criar a .venv: %v", err)
		}
	}
	fmt.Println("A instalar as bibliotecas (pode demorar uns minutos)...")
	_ = run(venvPy, "-m", "pip", "install", "--upgrade", "pip")
	if err := run(venvPy, "-m", "pip", "install", "-r", "requirements.txt"); err != nil {
		return fmt.Errorf("pip falhou: %v", err)
	}
	return os.WriteFile(stampFile, []byte(requirementsHash()), 0o644)
}

// O comando do Python 3.12 (["py", "-3.12"] ou o caminho do python.exe), ou nil.
func findPython() ([]string, error) {
	if out, err := exec.Command("py", "-3.12", "--version").CombinedOutput(); err == nil &&
		strings.Contains(string(out), "3.12") {
		return []string{"py", "-3.12"}, nil
	}
	for _, base := range []string{os.Getenv("LOCALAPPDATA") + "\\Programs\\Python\\Python312",
		os.Getenv("ProgramFiles") + "\\Python312", "C:\\Python312"} {
		p := filepath.Join(base, "python.exe")
		if _, err := os.Stat(p); err == nil {
			return []string{p}, nil
		}
	}
	return nil, nil
}

// ---------------------------------------------------------------- painel
func panel() {
	if _, err := os.Stat("main.py"); err != nil {
		message("Painel", "O Painel.exe tem de ficar na pasta do painel (ao lado do main.py).")
		return
	}
	py, _ := findPython()
	if py == nil {
		if err := downloadPythonPassive(); err != nil {
			message("Painel", "Não foi possível instalar o Python 3.12: "+err.Error()+
				"\n\nInstale-o em https://www.python.org/downloads/ e abra o Painel.exe de novo.")
			return
		}
		if py, _ = findPython(); py == nil {
			message("Painel", "O Python 3.12 não ficou instalado. Instale-o em https://www.python.org/downloads/")
			return
		}
	}
	// pythonw: sem janela preta
	var args []string
	if py[0] == "py" {
		args = append([]string{"pyw"}, py[1:]...)
	} else {
		args = []string{filepath.Join(filepath.Dir(py[0]), "pythonw.exe")}
	}
	_ = os.MkdirAll("logs", 0o755)
	logPath := filepath.Join("logs", "painel.log")
	logf, _ := os.Create(logPath)
	cmd := exec.Command(args[0], append(args[1:], "main.py")...)
	if logf != nil {
		cmd.Stdout = logf
		cmd.Stderr = logf
	}
	err := cmd.Run()
	if logf != nil {
		logf.Close()
	}
	if err != nil {
		message("Painel", "O painel fechou com um erro.\n\n"+tail(logPath, 1500))
	}
}

// Instalação com a janela de progresso do Python (o Painel.exe não tem consola).
func downloadPythonPassive() error {
	tmp := filepath.Join(os.TempDir(), "python-"+pythonVersion+"-amd64.exe")
	resp, err := http.Get(pythonURL)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("python.org respondeu %s", resp.Status)
	}
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, resp.Body); err != nil {
		f.Close()
		return err
	}
	f.Close()
	return exec.Command(tmp, "/passive", "InstallAllUsers=0", "PrependPath=0", "Include_launcher=1",
		"Include_test=0", "Include_tcltk=1", "Shortcuts=0").Run()
}

func downloadPython() error {
	tmp := filepath.Join(os.TempDir(), "python-"+pythonVersion+"-amd64.exe")
	resp, err := http.Get(pythonURL)
	if err != nil {
		return fmt.Errorf("sem internet para baixar o Python: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("python.org respondeu %s", resp.Status)
	}
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, resp.Body); err != nil {
		f.Close()
		return err
	}
	f.Close()
	fmt.Println("A instalar o Python", pythonVersion, "(só para este utilizador)...")
	// instalação silenciosa, sem precisar de administrador
	return run(tmp, "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_launcher=1",
		"Include_test=0", "Shortcuts=0")
}

func run(args ...string) error {
	cmd := exec.Command(args[0], args[1:]...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	return cmd.Run()
}

func requirementsHash() string {
	data, _ := os.ReadFile("requirements.txt")
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func installed() bool {
	if _, err := os.Stat(filepath.Join(".venv", "Scripts", "pythonw.exe")); err != nil {
		return false
	}
	stamp, err := os.ReadFile(stampFile)
	return err == nil && strings.TrimSpace(string(stamp)) == requirementsHash()
}

// ---------------------------------------------------------------- jogar
func play() {
	if _, err := os.Stat("main.py"); err != nil {
		message("Jogo", "O Jogar.exe tem de ficar na pasta do jogo (ao lado do main.py).")
		return
	}
	if !installed() {
		if _, err := os.Stat("Instalar.exe"); err != nil {
			message("Jogo", "Falta instalar o jogo e o Instalar.exe não está nesta pasta.")
			return
		}
		inst, _ := filepath.Abs("Instalar.exe")
		cmd := exec.Command(inst)
		cmd.Env = append(os.Environ(), "JOGO_SEM_PAUSA=1")
		cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x00000010} // nova consola
		if err := cmd.Run(); err != nil || !installed() {
			message("Jogo", "A instalação não terminou. Abra o Instalar.exe para ver o erro.")
			return
		}
	}
	_ = os.MkdirAll("logs", 0o755)
	logPath := filepath.Join("logs", "jogo.log")
	logf, err := os.Create(logPath)
	if err != nil {
		logf = nil
	}
	cmd := exec.Command(filepath.Join(".venv", "Scripts", "pythonw.exe"), "-X", "utf8", "main.py")
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8", "PYTHONUNBUFFERED=1")
	if logf != nil {
		cmd.Stdout = logf
		cmd.Stderr = logf
	}
	err = cmd.Run()
	if logf != nil {
		logf.Close()
	}
	if err != nil {
		message("Jogo", "O jogo fechou com um erro.\n\n"+tail(logPath, 1500)+
			"\n\n(registo completo em "+logPath+")")
	}
}

func tail(path string, n int) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	if len(data) > n {
		data = data[len(data)-n:]
	}
	return string(data)
}

func message(title, text string) {
	user32 := syscall.NewLazyDLL("user32.dll")
	box := user32.NewProc("MessageBoxW")
	t, _ := syscall.UTF16PtrFromString(title)
	m, _ := syscall.UTF16PtrFromString(text)
	box.Call(0, uintptr(unsafe.Pointer(m)), uintptr(unsafe.Pointer(t)), 0x10)
}
