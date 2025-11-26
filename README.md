# LUW-Shell
The Shell that unifies shell scripting and coding

Quick start:
-----------------------------------------------------
Launch the Windows Terminal and open a new LUW Shell Instance.
Then, just type a command.

A good start is with:
`cowsay --animal dragon --rgb 230 230 0 upper "I'm a little dragon"`

Since we have autocomplete, you can type `!multithread ` to view some examples.

Use:
---------------------------------------------------------------
Not every command will be explained here, since many are covered by autocomplete

`!multithread` or `!mt`

Sets the following command into the waiting list of a cluster. Multiple commands seperated by &

`!pwsh`

Executes the following command with PowerShell

`!cmd`

Executes the following command with CMD

`help`

Views the usage of most commands

Creating executables and scripts
----------------------------------------------------------------------
To create a script, just create a file script.latin (Or which other name you want) and then execute

`<PathToThelatin.exe> --script <pathtoyourscript.latin>`
or double click it in the file explorer. If you chose the file explorer, mind that you won't see the output because the terminal automatically closes after it finishes

If you want to compile a script execute

`<PathToThelatin.exe> --compile <pathtoyourscript>`

(The compiler only accepts .latin and .py files and will output a .le file in the directory it ran from)

and then execute the compiled script with

`<PathToThelatin.exe> --binary <pathtoyourscript.le>` or double click it in the file explorer If you chose the file explorer, mind that you won't see the output because the terminal automatically closes after it finishes
