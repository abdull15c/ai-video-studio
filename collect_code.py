import os

def collect_code():
    output_file = "all_project_code.txt"
    
    # Directories to exclude from the dump (like virtual envs, git, cache)
    exclude_dirs = [".git", "__pycache__", "venv", "env", "node_modules", ".vscode"]
    # Files to exclude from the dump
    exclude_files = [output_file, "collect_code.py", "requirements.txt", "topics.txt", "all_root_code.txt"]

    # Only include these extensions or specific files
    valid_extensions = [".py", ".html", ".js", ".css", ".md"]
    valid_files = ["Dockerfile", "docker-compose.yml"]

    with open(output_file, "w", encoding="utf-8") as outfile:
        # Iterate over all files in the project directory
        for root, dirs, files in os.walk("."):
            # Exclude specific directories
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            
            for filename in files:
                if filename in exclude_files:
                    continue
                
                # Check extension or exact name
                _, ext = os.path.splitext(filename)
                if ext in valid_extensions or filename in valid_files:
                    filepath = os.path.join(root, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as infile:
                            content = infile.read()
                            # Print a separator for each file
                            outfile.write(f"========================================\n")
                            outfile.write(f"FILE: {filepath}\n")
                            outfile.write(f"========================================\n\n")
                            outfile.write(content)
                            outfile.write(f"\n\n")
                    except Exception as e:
                        print(f"Error reading {filepath}: {e}")
                        
    print(f"✅ Успешно собраны файлы всего проекта в {output_file}")

if __name__ == "__main__":
    collect_code()
